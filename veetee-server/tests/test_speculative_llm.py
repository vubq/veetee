import asyncio
import unittest

from config.settings import AppConfig
from core.tools.base import ToolDescriptor
from core.turn_events import (
    CompletedEvent,
    ControlEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)
from tests.test_turn_lifecycle import (
    FakeWebSocket,
    SessionForTest,
    TwoFrameTTS,
)


class CountingNativeLLM:
    def __init__(self):
        self.calls = []

    async def stream_turn(
        self,
        messages,
        *,
        tools=None,
        detect_end_intent=True,
        tool_choice=None,
    ):
        self.calls.append({
            "messages": [dict(item) for item in messages],
            "tools": list(tools or []),
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        yield ControlEvent()
        yield SpeechSegmentEvent("Mình nghe rõ.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class SpeculativeToolLLM:
    def __init__(self):
        self.calls = 0

    async def stream_turn(
        self,
        messages,
        *,
        tools=None,
        detect_end_intent=True,
        tool_choice=None,
    ):
        self.calls += 1
        if self.calls == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(
                call_id="spec-read-1",
                name="spec_read",
                arguments={},
            )
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Đã đọc dữ liệu.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class EarlySpeechBlockingLLM:
    def __init__(self):
        self.first_speech_ready = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def stream_turn(
        self,
        messages,
        *,
        tools=None,
        detect_end_intent=True,
        tool_choice=None,
    ):
        self.calls += 1
        yield ControlEvent()
        yield SpeechSegmentEvent("Phản hồi sớm.", emotion="neutral")
        self.first_speech_ready.set()
        await self.release.wait()
        yield CompletedEvent(finish_reason="stop")


class CountingPrefetchTTS:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.cancelled = 0

    async def stream_sentence_to_opus(
        self,
        text,
        cancel_event=None,
        *,
        priority="live",
        queue_deadline_seconds=None,
    ):
        self.calls.append({
            "text": text,
            "priority": priority,
            "queue_deadline_seconds": queue_deadline_seconds,
        })
        self.started.set()
        for frame in (b"prefetch-1", b"prefetch-2"):
            if cancel_event is not None and cancel_event.is_set():
                self.cancelled += 1
                return
            yield frame


class BlockingNativeLLM:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def stream_turn(
        self,
        messages,
        *,
        tools=None,
        detect_end_intent=True,
        tool_choice=None,
    ):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield ControlEvent()
        yield SpeechSegmentEvent("Không nên phát.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


def make_speculative_session(llm, *, tts=None, speculative_tts=False):
    config = AppConfig()
    config.asr.speculative_inference_enabled = True
    config.asr.speculative_llm_enabled = True
    config.asr.speculative_llm_min_confidence = 0.95
    config.tts.speculative_prefetch_enabled = bool(speculative_tts)
    websocket = FakeWebSocket()
    session = SessionForTest(
        websocket,
        config,
        tts or TwoFrameTTS(),
        llm,
    )
    return session, websocket


class SpeculativeLLMTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_speculative_turn_has_no_side_effect_before_final_and_is_reused(self):
        llm = CountingNativeLLM()
        session, websocket = make_speculative_session(llm)

        await session._on_asr_speculative_transcript(
            "Mấy giờ rồi?",
            0.99,
            session._capture_generation,
        )
        speculative_task = session._speculative_llm_task
        self.assertIsNotNone(speculative_task)
        await asyncio.wait_for(speculative_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(websocket.sent, [])
        self.assertEqual(session.dialogue.get_messages_for_llm(), [])

        await session._on_asr_transcript(
            "Mấy giờ rồi?",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)
        await asyncio.wait_for(turn, timeout=1.0)

        # Exact final transcript reuses the already-finished AI turn instead
        # of making a second provider request.
        self.assertEqual(len(llm.calls), 1)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))
        self.assertEqual(
            session.dialogue.get_messages_for_llm()[0]["content"],
            "Mấy giờ rồi?",
        )

    async def test_speculative_tool_event_has_no_side_effect_until_final_commit(self):
        executed = []

        async def read_handler(arguments):
            executed.append(dict(arguments))
            return {"value": 42}

        llm = SpeculativeToolLLM()
        session, websocket = make_speculative_session(llm)
        session.tool_registry.register(ToolDescriptor(
            name="spec_read",
            description="test read",
            input_schema={"type": "object", "additionalProperties": False},
            handler=read_handler,
            read_only=True,
            idempotent=True,
        ))

        await session._on_asr_speculative_transcript(
            "Đọc dữ liệu",
            0.99,
            session._capture_generation,
        )
        speculative_task = session._speculative_llm_task
        self.assertIsNotNone(speculative_task)
        await asyncio.wait_for(speculative_task, timeout=1.0)

        # Provider work may finish early, but buffered ToolCallReadyEvent must
        # not execute before ASR final commits the exact transcript.
        self.assertEqual(executed, [])
        self.assertEqual(websocket.sent, [])

        await session._on_asr_transcript(
            "Đọc dữ liệu",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)
        await asyncio.wait_for(turn, timeout=1.0)

        self.assertEqual(executed, [{}])
        self.assertEqual(llm.calls, 2)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))

    async def test_final_adopts_stream_before_speculative_provider_completes(self):
        llm = EarlySpeechBlockingLLM()
        session, websocket = make_speculative_session(llm)

        await session._on_asr_speculative_transcript(
            "Mấy giờ rồi?",
            0.99,
            session._capture_generation,
        )
        await asyncio.wait_for(llm.first_speech_ready.wait(), timeout=1.0)
        speculative_task = session._speculative_llm_task
        self.assertIsNotNone(speculative_task)
        self.assertFalse(speculative_task.done())
        self.assertEqual(websocket.sent, [])

        await session._on_asr_transcript(
            "Mấy giờ rồi?",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)

        # The already-buffered first speech segment should reach TTS while the
        # speculative provider stream is still waiting for its terminal event.
        for _ in range(20):
            if any(isinstance(item, bytes) for item in websocket.sent):
                break
            await asyncio.sleep(0)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))
        self.assertFalse(turn.done())
        self.assertEqual(llm.calls, 1)

        llm.release.set()
        await asyncio.wait_for(turn, timeout=1.0)

    async def test_speech_resume_cancels_inflight_speculative_llm(self):
        llm = BlockingNativeLLM()
        session, websocket = make_speculative_session(llm)

        await session._on_asr_speculative_transcript(
            "Xin chào",
            0.99,
            session._capture_generation,
        )
        await asyncio.wait_for(llm.started.wait(), timeout=1.0)
        task = session._speculative_llm_task
        self.assertIsNotNone(task)

        await session._on_asr_speculative_invalidated(
            session._capture_generation
        )
        await asyncio.sleep(0)

        self.assertIsNone(session._speculative_llm_task)
        self.assertTrue(task.cancelled() or llm.cancelled)
        self.assertEqual(websocket.sent, [])

    async def test_final_transcript_mismatch_discards_speculation_and_runs_live_turn(self):
        llm = CountingNativeLLM()
        session, websocket = make_speculative_session(llm)

        await session._on_asr_speculative_transcript(
            "Xin chào",
            0.99,
            session._capture_generation,
        )
        task = session._speculative_llm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(len(llm.calls), 1)

        await session._on_asr_transcript(
            "Xin chào bạn",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)
        await asyncio.wait_for(turn, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))

    async def test_speculative_tts_prefetch_never_sends_before_final_and_is_reused(self):
        llm = CountingNativeLLM()
        tts = CountingPrefetchTTS()
        session, websocket = make_speculative_session(
            llm,
            tts=tts,
            speculative_tts=True,
        )

        await session._on_asr_speculative_transcript(
            "Mấy giờ rồi?",
            0.99,
            session._capture_generation,
        )
        await asyncio.wait_for(tts.started.wait(), timeout=1.0)
        task = session._speculative_llm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)

        # TTS work is allowed to happen early, but protocol/audio remains
        # side-effect free until exact ASR final commits the turn.
        self.assertEqual(len(tts.calls), 1)
        self.assertEqual(websocket.sent, [])

        await session._on_asr_transcript(
            "Mấy giờ rồi?",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)
        await asyncio.wait_for(turn, timeout=1.0)

        self.assertEqual(len(tts.calls), 1)
        binary = [item for item in websocket.sent if isinstance(item, bytes)]
        self.assertEqual(binary, [b"prefetch-1", b"prefetch-2"])

    async def test_speculative_tts_mismatch_discards_prefetch_and_uses_live_tts(self):
        llm = CountingNativeLLM()
        tts = CountingPrefetchTTS()
        session, websocket = make_speculative_session(
            llm,
            tts=tts,
            speculative_tts=True,
        )

        await session._on_asr_speculative_transcript(
            "Xin chào",
            0.99,
            session._capture_generation,
        )
        await asyncio.wait_for(tts.started.wait(), timeout=1.0)
        task = session._speculative_llm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(websocket.sent, [])

        await session._on_asr_transcript(
            "Xin chào bạn",
            True,
            True,
            session._capture_generation,
        )
        turn = session.current_turn_task
        self.assertIsNotNone(turn)
        await asyncio.wait_for(turn, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(tts.calls), 2)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))

    async def test_tool_only_speculation_does_not_start_tts_prefetch(self):
        executed = []

        async def read_handler(arguments):
            executed.append(dict(arguments))
            return {"value": 42}

        llm = SpeculativeToolLLM()
        tts = CountingPrefetchTTS()
        session, websocket = make_speculative_session(
            llm,
            tts=tts,
            speculative_tts=True,
        )
        session.tool_registry.register(ToolDescriptor(
            name="spec_read",
            description="test read",
            input_schema={"type": "object", "additionalProperties": False},
            handler=read_handler,
            read_only=True,
            idempotent=True,
        ))

        await session._on_asr_speculative_transcript(
            "Đọc dữ liệu",
            0.99,
            session._capture_generation,
        )
        task = session._speculative_llm_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)

        self.assertEqual(tts.calls, [])
        self.assertEqual(websocket.sent, [])
        self.assertEqual(executed, [])


if __name__ == "__main__":
    unittest.main()
