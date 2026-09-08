import asyncio
import os
import tempfile
import unittest

from config.settings import AppConfig
from core.context_builder import ContextBuilder
from core.memory.models import MemoryProposal
from core.memory.policy import MemoryPolicy
from core.memory.retrieval import MemoryRetriever
from core.memory.store import MemoryStore
from core.session import ClientSession


class _FakeWebSocket:
    request = None

    async def send(self, payload):
        return None


class _FakeASR:
    async def start(self):
        return None

    async def stop(self):
        return None


class _MemorySession(ClientSession):
    def _create_asr(self):
        return _FakeASR()


class _UnusedTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        if False:
            yield b""


class _UnusedLLM:
    async def stream_chat(self, messages):
        if False:
            yield "", None


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "memory.sqlite3")
        self.store = MemoryStore(self.path)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_owner_isolation_persistence_revision_and_forget(self):
        revision1 = await self.store.upsert(
            owner_id="owner-a", scope="personal", kind="preference", key="drink",
            value="thích cà phê", source_turn_id="t1", evidence="Tôi thích cà phê",
        )
        revision2 = await self.store.upsert(
            owner_id="owner-a", scope="personal", kind="preference", key="drink",
            value="thích trà", source_turn_id="t2", evidence="Giờ tôi thích trà",
        )
        await self.store.upsert(
            owner_id="owner-b", scope="personal", kind="preference", key="drink",
            value="thích nước lọc", source_turn_id="t3", evidence="Tôi thích nước lọc",
        )
        self.assertEqual((revision1, revision2), (1, 2))

        reopened = MemoryStore(self.path)
        owner_a = await reopened.list_active(owner_id="owner-a", scope="personal")
        owner_b = await reopened.list_active(owner_id="owner-b", scope="personal")
        self.assertEqual([fact.value for fact in owner_a], ["thích trà"])
        self.assertEqual([fact.value for fact in owner_b], ["thích nước lọc"])

        changed = await reopened.tombstone(owner_id="owner-a", scope="personal", keys=["drink"])
        self.assertEqual(changed, 1)
        self.assertEqual(await reopened.list_active(owner_id="owner-a", scope="personal"), [])
        self.assertEqual(len(await reopened.list_active(owner_id="owner-b", scope="personal")), 1)

    async def test_retrieval_matches_vietnamese_with_or_without_diacritics(self):
        await self.store.upsert(
            owner_id="owner", scope="personal", kind="fact", key="food",
            value="thích phở bò", source_turn_id="t1", evidence="Tôi thích phở bò",
        )
        retriever = MemoryRetriever(self.store)
        facts = await retriever.retrieve(
            owner_id="owner", scope="personal", query="toi thich pho gi",
        )
        self.assertEqual(facts[0].value, "thích phở bò")

    async def test_context_builder_degrades_when_memory_backend_fails(self):
        class BrokenRetriever:
            async def retrieve(self, **kwargs):
                raise RuntimeError("database locked")

        builder = ContextBuilder(BrokenRetriever(), lookup_timeout_ms=10)
        messages = [{"role": "user", "content": "Xin chào"}]
        self.assertEqual(
            await builder.build(messages, query="Xin chào", owner_id="owner", session_memory=[]),
            messages,
        )

    def test_secret_is_rejected_from_explicit_durable_memory(self):
        self.assertIsNone(MemoryPolicy.explicit_proposal("Nhớ rằng API key của tôi là abc123"))
        proposal = MemoryPolicy.explicit_proposal("Nhớ rằng tôi thích màu xanh")
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.action, "upsert")

    def test_explicit_memory_parser_rejects_negation_quotes_and_ambiguous_mentions(self):
        rejected = [
            "Đừng quên mọi thứ tôi đã nói.",
            "Tôi không nhớ tên bạn.",
            'Giải thích câu "nhớ tôi thích cà phê".',
            "Giả sử tôi nói nhớ rằng tôi thích màu đỏ thì sao?",
        ]
        for text in rejected:
            with self.subTest(text=text):
                self.assertIsNone(MemoryPolicy.explicit_proposal(text))

        proposal = MemoryPolicy.explicit_proposal("Nhớ rằng tôi không thích cà phê.")
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.action, "upsert")
        self.assertEqual(proposal.value, "tôi không thích cà phê")

    async def test_durable_write_failure_does_not_mutate_session_memory(self):
        class BrokenStore:
            async def upsert(self, **kwargs):
                raise RuntimeError("write failed")

            async def tombstone(self, **kwargs):
                raise RuntimeError("write failed")

            async def list_active(self, **kwargs):
                return []

        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._memory_owner_id = "owner"
        session._memory_store = BrokenStore()
        session._session_memory[:] = ["thích trà"]

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            await session._apply_memory_proposal(
                MemoryProposal(action="upsert", key="food", value="thích phở"),
                turn_id="t1",
            )
        self.assertEqual(session._session_memory, ["thích trà"])

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            await session._apply_memory_proposal(
                MemoryProposal(action="forget_all"),
                turn_id="t2",
            )
        self.assertEqual(session._session_memory, ["thích trà"])

    async def test_durable_forget_commits_before_session_projection_changes(self):
        class Fact:
            key = "drink"
            value = "thích trà"

        class BrokenDeleteStore:
            async def list_active(self, **kwargs):
                return [Fact()]

            async def tombstone(self, **kwargs):
                raise RuntimeError("delete failed")

        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._memory_owner_id = "owner"
        session._memory_store = BrokenDeleteStore()
        session._session_memory[:] = ["thích trà", "thích cà phê"]

        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            await session._apply_memory_proposal(
                MemoryProposal(action="forget", value="thích trà"),
                turn_id="t3",
            )
        self.assertEqual(session._session_memory, ["thích trà", "thích cà phê"])

    async def test_forget_not_found_is_not_reported_as_applied(self):
        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._session_memory[:] = ["thích trà"]

        result = await session._apply_memory_proposal(
            MemoryProposal(action="forget", value="thích cà phê"),
            turn_id="t4",
        )

        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.applied)
        self.assertFalse(result.changed)
        self.assertEqual(session._session_memory, ["thích trà"])


if __name__ == "__main__":
    unittest.main()
