import asyncio
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

    async def test_first_latency_sample_is_not_blended_with_fake_prior(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        router.note_latency("gA", 0.4)
        snapshot = router.latency_snapshot()
        self.assertEqual(snapshot["gA"]["samples"], 1)
        self.assertEqual(snapshot["gA"]["ewma_latency_ms"], 400.0)
        self.assertEqual(snapshot["gA"]["ewma_abs_deviation_ms"], 0.0)

    async def test_unseen_group_is_explored_before_reusing_measured_group(self):
        clock = FakeClock()
        router, _ = make_router(clock)
        router.note_latency("gA", 0.3)
        lease = await router.acquire(100, purpose="chat")
        self.assertEqual(lease.alias, "B")
        await router.settle_ok(lease, actual_tokens=50)

    async def test_jitter_penalty_prefers_stable_route(self):
        clock = FakeClock()
        router, _ = make_router(clock, ewma_alpha=0.5, jitter_penalty=1.0)
        router.note_latency("gA", 0.25)
        router.note_latency("gA", 0.65)
        router.note_latency("gB", 0.48)
        router.note_latency("gB", 0.48)
        lease = await router.acquire(100, purpose="chat")
        self.assertEqual(lease.alias, "B")
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

    async def test_busy_discovery_group_waits_for_headers_then_admits(self):
        ledger = QuotaLedger(discovery_max_inflight=1)
        targets = [
            RouteTarget(
                alias="B",
                api_key="kB",
                quota_group="gB",
                base_url="https://x",
            )
        ]
        router = GroqRouter(
            targets,
            ledger,
            admission_wait_ms=0,
            discovery_wait_ms=300,
        )

        first = await router.acquire(100, purpose="chat")
        second_task = asyncio.create_task(
            router.acquire(100, purpose="chat")
        )
        await asyncio.sleep(0.05)
        self.assertFalse(second_task.done())

        # Response headers from the first request end discovery. The second
        # request may now reserve the known quota even while the first request
        # is still in flight.
        await ledger.note_limits("gB", {"rpm": 100, "tpm": 10000})
        second = await asyncio.wait_for(second_task, timeout=0.25)
        self.assertEqual(second.alias, "B")

        await router.settle_ok(first, actual_tokens=50)
        await router.settle_ok(second, actual_tokens=50)

    async def test_known_exhaustion_does_not_pay_discovery_wait(self):
        ledger = QuotaLedger({"gA": {"tpm": 50}})
        targets = [
            RouteTarget(
                alias="A",
                api_key="kA",
                quota_group="gA",
                base_url="https://x",
            )
        ]
        router = GroqRouter(
            targets,
            ledger,
            admission_wait_ms=0,
            discovery_wait_ms=300,
        )
        started = asyncio.get_running_loop().time()
        with self.assertRaises(QuotaExhausted):
            await router.acquire(100, purpose="chat")
        elapsed = asyncio.get_running_loop().time() - started
        self.assertLess(elapsed, 0.1)

    async def test_routing_policy_hot_configures_without_rebuild(self):
        ledger = QuotaLedger(discovery_max_inflight=1)
        targets = [
            RouteTarget(
                alias="A",
                api_key="kA",
                quota_group="gA",
                base_url="https://x",
            )
        ]
        router = GroqRouter(
            targets,
            ledger,
            headroom_pct=10,
            admission_wait_ms=50,
            discovery_wait_ms=300,
        )

        await router.configure(
            headroom_pct=12.5,
            admission_wait_ms=80,
            discovery_wait_ms=900,
            discovery_max_inflight=2,
            inflight_penalty_s=0.6,
            ewma_alpha=0.4,
            jitter_penalty=0.9,
        )
        snapshot = router.config_snapshot()
        self.assertEqual(snapshot["headroom_pct"], 12.5)
        self.assertEqual(snapshot["admission_wait_ms"], 80.0)
        self.assertEqual(snapshot["discovery_wait_ms"], 900.0)
        self.assertEqual(snapshot["inflight_penalty_s"], 0.6)
        self.assertEqual(snapshot["latency_ewma_alpha"], 0.4)
        self.assertEqual(snapshot["latency_jitter_penalty"], 0.9)

        # The ledger update is live too: two unknown-quota discovery leases
        # may coexist after increasing discovery_max_inflight from 1 to 2.
        first = await router.acquire(10, purpose="chat")
        second = await router.acquire(10, purpose="chat")
        self.assertEqual(first.quota_group, "gA")
        self.assertEqual(second.quota_group, "gA")
        await router.settle_uncertain(first)
        await router.settle_uncertain(second)

    async def test_double_settle_releases_once(self):
        clock = FakeClock()
        ledger = QuotaLedger({"gA": {"rpm": 1}}, clock=clock)
        targets = [RouteTarget(alias="A", api_key="kA", quota_group="gA",
                               base_url="https://x")]
        router = GroqRouter(targets, ledger, clock=clock)
        lease = await router.acquire(10, purpose="chat")
        await router.settle_ok(lease, actual_tokens=5)
        # Second settle (e.g. generator finally after explicit settle) no-ops:
        # in_flight stays released exactly once, token charge stays actual.
        await router.settle_uncertain(lease)
        snap = await ledger.snapshot("gA")
        self.assertEqual(snap["in_flight"], 0)
        clock.now += 61.0  # rpm window passes; budget usable again.
        self.assertIsNotNone(await ledger.try_reserve("gA", tokens=5))

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
