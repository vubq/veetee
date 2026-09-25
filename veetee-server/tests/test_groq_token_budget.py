import tempfile
import unittest

from core.management_store import ManagementStore
from core.providers.llm.quota import QuotaLedger
from core.providers.llm.router import GroqRouter, QuotaExhausted, RouteTarget


class GroqTokenBudgetStoreTests(unittest.TestCase):
    def test_usage_persists_for_same_key_and_resets_for_replaced_or_readded_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/manager-state.json"
            store = ManagementStore(path)
            env_key = "GROQ_API_KEY_A"

            public = store.update_runtime({
                env_key: "secret-a",
                f"groq.token_limit.{env_key}": 1000,
            })
            usage = public[env_key]["token_usage"]
            self.assertEqual(usage["period"], "day")
            self.assertTrue(usage["day"])
            self.assertEqual(usage["used"], 0)
            self.assertEqual(usage["limit"], 1000)
            self.assertEqual(usage["remaining"], 1000)
            first = store.ensure_groq_credential(env_key, "secret-a")
            reservation = store.try_reserve_groq_tokens(
                env_key, first["instance_id"], 300
            )
            self.assertIsNotNone(reservation)
            charged = store.settle_groq_token_reservation(
                reservation, outcome="ok", actual_tokens=250
            )
            self.assertEqual(charged, 250)
            store.flush()

            # Same credential keeps today's accounting through reloads.
            reloaded = ManagementStore(path)
            public = reloaded.runtime_public()
            self.assertEqual(public[env_key]["token_usage"]["used"], 250)
            self.assertEqual(public[env_key]["token_usage"]["remaining"], 750)
            same = reloaded.ensure_groq_credential(env_key, "secret-a")
            self.assertEqual(same["instance_id"], first["instance_id"])

            # Replacing the secret creates a fresh credential identity.
            reloaded.update_runtime({env_key: "secret-b"})
            replaced = reloaded.ensure_groq_credential(env_key, "secret-b")
            self.assertNotEqual(replaced["instance_id"], first["instance_id"])
            self.assertEqual(replaced["used_tokens"], 0)
            self.assertEqual(replaced["token_limit"], 0)

            # Deleting removes both secret and usage metadata. Re-adding even
            # the same secret is a brand-new instance.
            reloaded.update_runtime({
                f"groq.token_limit.{env_key}": 500,
            })
            old_readded_id = replaced["instance_id"]
            reloaded.update_runtime({env_key: None})
            self.assertNotIn(env_key, reloaded.runtime_public())
            reloaded.update_runtime({env_key: "secret-b"})
            fresh = reloaded.ensure_groq_credential(env_key, "secret-b")
            self.assertNotEqual(fresh["instance_id"], old_readded_id)
            self.assertEqual(fresh["used_tokens"], 0)
            self.assertEqual(fresh["token_limit"], 0)

    def test_daily_usage_resets_when_utc_day_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + "/state.json")
            env_key = "GROQ_API_KEY_A"
            store.update_runtime({
                env_key: "secret-a",
                f"groq.token_limit.{env_key}": 1000,
            })
            state = store.groq_state_raw()
            state[env_key]["used_tokens"] = 900
            state[env_key]["usage_day"] = "2000-01-01"
            store.restore_groq_state(state)

            usage = store.runtime_public()[env_key]["token_usage"]
            self.assertEqual(usage["used"], 0)
            self.assertEqual(usage["remaining"], 1000)
            self.assertNotEqual(usage["day"], "2000-01-01")

    def test_uncertain_request_charges_reserved_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + "/state.json")
            env_key = "GROQ_API_KEY_A"
            store.update_runtime({
                env_key: "secret-a",
                f"groq.token_limit.{env_key}": 1000,
            })
            row = store.ensure_groq_credential(env_key, "secret-a")
            reservation = store.try_reserve_groq_tokens(
                env_key, row["instance_id"], 320
            )
            store.settle_groq_token_reservation(
                reservation, outcome="uncertain"
            )
            self.assertEqual(
                store.runtime_public()[env_key]["token_usage"]["used"], 320
            )


class GroqTokenBudgetRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_skips_key_whose_daily_limit_cannot_fit_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + "/state.json")
            store.update_runtime({
                "GROQ_API_KEY_A": "secret-a",
                "groq.token_limit.GROQ_API_KEY_A": 150,
                "GROQ_API_KEY_B": "secret-b",
            })
            ledger = QuotaLedger({
                "gA": {"rpm": 100, "tpm": 10000},
                "gB": {"rpm": 100, "tpm": 10000},
            })
            router = GroqRouter([
                RouteTarget(
                    alias="A", api_key="secret-a", quota_group="gA",
                    base_url="https://x", env_key="GROQ_API_KEY_A",
                ),
                RouteTarget(
                    alias="B", api_key="secret-b", quota_group="gB",
                    base_url="https://x", env_key="GROQ_API_KEY_B",
                ),
            ], ledger, usage_store=store, admission_wait_ms=0)

            router.note_latency("gA", 0.1)
            router.note_latency("gB", 1.0)
            first = await router.acquire(100, purpose="chat")
            self.assertEqual(first.alias, "A")
            await router.settle_ok(first, actual_tokens=90)

            # Only 60 tokens remain on A, so an estimated 80-token request
            # must route to B without attempting A.
            second = await router.acquire(80, purpose="chat")
            self.assertEqual(second.alias, "B")
            await router.settle_ok(second, actual_tokens=70)
            await __import__("asyncio").sleep(0.05)

            public = store.runtime_public()
            self.assertEqual(
                public["GROQ_API_KEY_A"]["token_usage"]["used"], 90
            )
            self.assertEqual(
                public["GROQ_API_KEY_A"]["token_usage"]["remaining"], 60
            )

    async def test_single_key_hard_limit_raises_capacity_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ManagementStore(directory + "/state.json")
            store.update_runtime({
                "GROQ_API_KEY_A": "secret-a",
                "groq.token_limit.GROQ_API_KEY_A": 50,
            })
            ledger = QuotaLedger({"gA": {"tpm": 10000}})
            router = GroqRouter([
                RouteTarget(
                    alias="A", api_key="secret-a", quota_group="gA",
                    base_url="https://x", env_key="GROQ_API_KEY_A",
                ),
            ], ledger, usage_store=store, admission_wait_ms=0)
            with self.assertRaises(QuotaExhausted):
                await router.acquire(100, purpose="chat")


if __name__ == "__main__":
    unittest.main()
