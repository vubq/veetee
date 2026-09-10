import unittest

from core.providers.llm.quota import QuotaLedger
from core.providers.llm.router import GroqRouter, QuotaExhausted, RouteTarget


class FakeClock:
    def __init__(self):
        self.now = 2000.0

    def __call__(self):
        return self.now


def make_router(clock, **overrides):
    ledger = QuotaLedger(
        {"gA": {"rpm": 100, "tpm": 10000}, "gB": {"rpm": 100, "tpm": 10000}},
        clock=clock,
    )
    targets = [
        RouteTarget(alias="A", api_key="kA", quota_group="gA",
                    base_url="https://x"),
        RouteTarget(alias="B", api_key="kB", quota_group="gB",
                    base_url="https://x"),
    ]
    return GroqRouter(targets, ledger, clock=clock, **overrides), ledger


class GroqRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_picks_lowest_predicted_latency(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        router.note_latency("gA", 0.2)
        router.note_latency("gB", 2.0)
        lease = await router.acquire(100, purpose="chat")
        self.assertEqual(lease.alias, "A")
        await router.settle_ok(lease, actual_tokens=50)

    async def test_skips_cooling_group(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        router.note_latency("gA", 0.1)
        router.note_latency("gB", 5.0)
        await router.cooldown("gA", 60.0, reason="429")
        lease = await router.acquire(100, purpose="chat")
        self.assertEqual(lease.alias, "B")

    async def test_exhausted_group_skipped_before_network(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"tpm": 50}, "gB": {"tpm": 10000}},
                             clock=clock)
        targets = [
            RouteTarget(alias="A", api_key="kA", quota_group="gA",
                        base_url="https://x"),
            RouteTarget(alias="B", api_key="kB", quota_group="gB",
                        base_url="https://x"),
        ]
        router = GroqRouter(targets, ledger, clock=clock)
        router.note_latency("gA", 0.1)
        lease = await router.acquire(5000, purpose="chat")
        self.assertEqual(lease.alias, "B")

    async def test_all_exhausted_raises_without_http(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        with self.assertRaises(QuotaExhausted):
            await router.acquire(10**9, purpose="chat")

    async def test_low_priority_leaves_headroom(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"tpm": 1000}}, clock=clock)
        targets = [RouteTarget(alias="A", api_key="kA", quota_group="gA",
                               base_url="https://x")]
        router = GroqRouter(targets, ledger, clock=clock,
                            headroom_pct=50.0, admission_wait_ms=0)
        lease = await router.acquire(401, purpose="chat")
        # 599 remaining of 1000 < 50% headroom (500) + 100 needed -> skip.
        with self.assertRaises(QuotaExhausted):
            await router.acquire(100, purpose="prewarm")
        await router.settle_ok(lease, actual_tokens=0)

    async def test_disable_alias_on_401(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        router.disable_alias("A", reason="401")
        lease = await router.acquire(10, purpose="chat")
        self.assertEqual(lease.alias, "B")

    async def test_aliases_never_leak_keys(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        inventory = router.aliases()
        self.assertEqual(
            inventory,
            [{"alias": "A", "quota_group": "gA", "enabled": True},
             {"alias": "B", "quota_group": "gB", "enabled": True}])
        self.assertNotIn("kA", str(inventory))


if __name__ == "__main__":
    unittest.main()
