import asyncio
import unittest

from core.providers.llm.quota import QuotaLedger
from core.providers.llm.token_budget import estimate_request_tokens


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class QuotaLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserve_and_reject_when_exhausted(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 2, "tpm": 100}}, clock=clock)
        self.assertIsNotNone(await ledger.try_reserve("gA", tokens=10))
        self.assertIsNotNone(await ledger.try_reserve("gA", tokens=10))
        # Third request exceeds rpm=2.
        self.assertIsNone(await ledger.try_reserve("gA", tokens=10))

    async def test_window_expiry_frees_budget(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 1}}, clock=clock)
        self.assertIsNotNone(await ledger.try_reserve("gA"))
        self.assertIsNone(await ledger.try_reserve("gA"))
        clock.now += 61.0
        self.assertIsNotNone(await ledger.try_reserve("gA"))

    async def test_rejected_release_restores_budget(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 1}}, clock=clock)
        rid = await ledger.try_reserve("gA")
        self.assertIsNotNone(rid)
        await ledger.settle("gA", rid, outcome="rejected")
        self.assertIsNotNone(await ledger.try_reserve("gA"))

    async def test_ok_settle_reconciles_to_actual(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"tpm": 100}}, clock=clock)
        rid = await ledger.try_reserve("gA", tokens=90)
        self.assertIsNotNone(rid)
        await ledger.settle("gA", rid, outcome="ok", actual_tokens=10)
        # 90 estimated -> 10 actual, so another 80 fits.
        self.assertIsNotNone(await ledger.try_reserve("gA", tokens=80))

    async def test_uncertain_keeps_charge(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"tpm": 100}}, clock=clock)
        rid = await ledger.try_reserve("gA", tokens=90)
        self.assertIsNotNone(rid)
        await ledger.settle("gA", rid, outcome="uncertain")
        self.assertIsNone(await ledger.try_reserve("gA", tokens=20))

    async def test_cooldown_blocks_group(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 100}}, clock=clock)
        await ledger.set_cooldown("gA", clock.now + 30.0, reason="429")
        self.assertIsNone(await ledger.try_reserve("gA"))
        clock.now += 31.0
        self.assertIsNotNone(await ledger.try_reserve("gA"))

    async def test_discovery_mode_bounds_inflight(self):
        clock = FakeClock()
        ledger = QuotaLedger({}, clock=clock, discovery_max_inflight=1)
        self.assertIsNotNone(await ledger.try_reserve("gNew", tokens=10**9))
        # Second concurrent reservation blocked until first settles.
        self.assertIsNone(await ledger.try_reserve("gNew", tokens=1))

    async def test_learned_limits_leave_discovery(self):
        clock = FakeClock()
        ledger = QuotaLedger({}, clock=clock, discovery_max_inflight=1)
        rid = await ledger.try_reserve("gNew", tokens=5)
        await ledger.settle("gNew", rid, outcome="ok", actual_tokens=5)
        await ledger.note_limits("gNew", {"rpm": 10, "tpm": 1000})
        snap = await ledger.snapshot("gNew")
        self.assertFalse(snap["discovery"])
        self.assertIsNotNone(await ledger.try_reserve("gNew", tokens=5))
        self.assertIsNotNone(await ledger.try_reserve("gNew", tokens=5))

    async def test_concurrent_reservations_never_overspend(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 10}}, clock=clock)

        async def attempt():
            return await ledger.try_reserve("gA")

        results = await asyncio.gather(*(attempt() for _ in range(100)))
        self.assertEqual(sum(1 for r in results if r is not None), 10)

    async def test_double_settle_is_idempotent(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 10}}, clock=clock)
        rid = await ledger.try_reserve("gA")
        await ledger.settle("gA", rid, outcome="ok", actual_tokens=5)
        await ledger.settle("gA", rid, outcome="ok", actual_tokens=5)
        snap = await ledger.snapshot("gA")
        self.assertEqual(snap["in_flight"], 0)
        # Unknown reservation ids are ignored safely.
        await ledger.settle("gA", 999999, outcome="rejected")

    async def test_unknown_dimension_rejected(self):
        ledger = QuotaLedger()
        with self.assertRaises(ValueError):
            ledger.configure_group("gBad", {"per_second": 5})


class TokenBudgetTests(unittest.TestCase):
    def test_estimate_covers_whole_request(self):
        messages = [
            {"role": "system", "content": "x" * 400},
            {"role": "user", "content": "hi"},
        ]
        tools = [{"type": "function", "function": {"name": "t"}}]
        estimate = estimate_request_tokens(messages, tools=tools, output_budget=100)
        # >= (400 + small texts + tools JSON) / 4 * 1.25 + output budget.
        self.assertGreaterEqual(estimate, 100 + 400 // 4)

    def test_estimate_empty(self):
        self.assertGreaterEqual(estimate_request_tokens([]), 1)


if __name__ == "__main__":
    unittest.main()
