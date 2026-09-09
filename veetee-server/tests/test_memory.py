import asyncio
import os
import tempfile
import unittest

from config.settings import AppConfig
from core.context_builder import ContextBuilder
from core.memory.models import MemoryProposal, SessionMemoryFact
from core.memory.policy import MemoryPolicy
from core.memory.retrieval import MemoryRetriever
from core.memory.store import MemoryStore
from core.session import ClientSession
from core.turn_events import CompletedEvent, ControlEvent, MemoryProposalEvent, SpeechSegmentEvent


class _FakeWebSocket:
    request = None

    async def send(self, payload):
        return None


class _FakeASR:
    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        return None


class _MemorySession(ClientSession):
    def _create_asr(self):
        return _FakeASR()


class _UnusedTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        yield b"frame"


class _UnusedLLM:
    async def stream_chat(self, messages):
        yield "ok", None


class _ScriptedMemoryLLM:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        script = self.scripts[self.calls]
        self.calls += 1
        for event in script:
            yield event


def _chat(text="Mình hiểu."):
    return [ControlEvent(), SpeechSegmentEvent(text), CompletedEvent()]


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "memory.sqlite3")
        self.store = MemoryStore(self.path)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_owner_isolation_persistence_revision_and_exact_forget(self):
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

        fact = owner_a[0]
        self.assertFalse(await reopened.tombstone_by_id(
            owner_id="owner-a", scope="personal", fact_id=fact.id, expected_revision=1
        ))
        self.assertTrue(await reopened.tombstone_by_id(
            owner_id="owner-a", scope="personal", fact_id=fact.id, expected_revision=fact.revision
        ))
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

    async def test_context_builder_exposes_opaque_ids_and_revisions(self):
        builder = ContextBuilder(None, lookup_timeout_ms=10)
        messages = [{"role": "user", "content": "Sở thích của tôi là gì?"}]
        facts = [SessionMemoryFact(id="session:abc", value="thích cà phê", revision=3)]
        built = await builder.build(
            messages,
            query="Sở thích của tôi là gì?",
            owner_id=None,
            session_memory=facts,
        )
        system = next(item for item in built if item.get("role") == "system")
        self.assertIn('"id":"session:abc"', system["content"])
        self.assertIn('"revision":3', system["content"])

    def test_memory_policy_only_validates_structured_action_names(self):
        self.assertTrue(MemoryPolicy.valid_action("upsert"))
        self.assertTrue(MemoryPolicy.valid_action("forget"))
        self.assertTrue(MemoryPolicy.valid_action("forget_all"))
        self.assertFalse(MemoryPolicy.valid_action("Nhớ rằng tôi thích màu xanh"))
        self.assertFalse(MemoryPolicy.valid_action(""))

    async def test_user_phrases_do_not_mutate_memory_without_ai_memory_event(self):
        scripts = [_chat() for _ in range(4)] + [[
            ControlEvent(intent="memory_remember"),
            MemoryProposalEvent(
                call_id="mem-1",
                action="upsert",
                value="thích uống cà phê",
                evidence="Lưu giúp tôi sở thích uống cà phê.",
            ),
            CompletedEvent(finish_reason="tool_calls"),
        ]]
        llm = _ScriptedMemoryLLM(scripts)
        config = AppConfig()
        config.tools.tool_result_synthesis = False
        session = _MemorySession(_FakeWebSocket(), config, _UnusedTTS(), llm)

        for text in (
            "Nhớ tôi không?",
            "Nhớ đừng lưu thông tin này.",
            "Nhớ lần trước mình nói gì không?",
            "Quên mật khẩu rồi thì làm sao?",
        ):
            await session._trigger_ai_turn(text)
            await asyncio.wait_for(session.current_turn_task, timeout=1)
            self.assertEqual(session._session_memory, [])

        await session._trigger_ai_turn("Lưu giúp tôi sở thích uống cà phê.")
        await asyncio.wait_for(session.current_turn_task, timeout=1)
        self.assertEqual(len(session._session_memory), 1)
        self.assertEqual(session._session_memory[0].value, "thích uống cà phê")

    async def test_session_edit_and_forget_require_exact_id_and_revision(self):
        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._session_memory[:] = [
            SessionMemoryFact(id="session:1", value="thích trà", revision=2)
        ]

        conflict = await session._apply_memory_proposal(
            MemoryProposal(action="upsert", fact_id="session:1", revision=1, value="thích cà phê"),
            turn_id="t1",
        )
        self.assertEqual(conflict.status, "revision_conflict")
        self.assertEqual(session._session_memory[0].value, "thích trà")

        updated = await session._apply_memory_proposal(
            MemoryProposal(action="upsert", fact_id="session:1", revision=2, value="thích cà phê"),
            turn_id="t2",
        )
        self.assertTrue(updated.applied)
        self.assertEqual(updated.revision, 3)
        self.assertEqual(session._session_memory[0].value, "thích cà phê")

        missing_target = await session._apply_memory_proposal(
            MemoryProposal(action="forget", value="thích cà phê"),
            turn_id="t3",
        )
        self.assertEqual(missing_target.status, "target_required")
        self.assertEqual(len(session._session_memory), 1)

        forgotten = await session._apply_memory_proposal(
            MemoryProposal(action="forget", fact_id="session:1", revision=3),
            turn_id="t4",
        )
        self.assertTrue(forgotten.applied)
        self.assertEqual(session._session_memory, [])

    async def test_durable_write_failure_does_not_mutate_session_memory(self):
        class BrokenStore:
            async def upsert(self, **kwargs):
                raise RuntimeError("write failed")

            async def get_active_by_key(self, **kwargs):
                return None

        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._memory_owner_id = "owner"
        session._memory_store = BrokenStore()
        original = SessionMemoryFact(id="session:old", value="thích trà", revision=1)
        session._session_memory[:] = [original]

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            await session._apply_memory_proposal(
                MemoryProposal(action="upsert", value="thích phở"),
                turn_id="t1",
            )
        self.assertEqual(session._session_memory, [original])

    async def test_durable_forget_failure_keeps_session_projection(self):
        class BrokenDeleteStore:
            async def tombstone_by_id(self, **kwargs):
                raise RuntimeError("delete failed")

        session = _MemorySession(_FakeWebSocket(), AppConfig(), _UnusedTTS(), _UnusedLLM())
        session._memory_owner_id = "owner"
        session._memory_store = BrokenDeleteStore()
        original = SessionMemoryFact(id="session:old", value="thích trà", revision=1)
        session._session_memory[:] = [original]

        with self.assertRaisesRegex(RuntimeError, "delete failed"):
            await session._apply_memory_proposal(
                MemoryProposal(action="forget", fact_id="durable:7", revision=4),
                turn_id="t3",
            )
        self.assertEqual(session._session_memory, [original])


if __name__ == "__main__":
    unittest.main()
