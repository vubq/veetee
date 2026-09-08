import asyncio
import json
import time
import unittest

from config.settings import AppConfig
from core.session import ClientSession, SessionState
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent, ToolCallReadyEvent


class FakeWebSocket:
    request = None

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class FakeASR:
    def __init__(self):
        self.invalidated_to = 0

    async def start(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        self.invalidated_to = capture_generation

    async def stop(self):
        return None


class SessionForTest(ClientSession):
    def _create_asr(self):
        return FakeASR()


class SingleClauseLLM:
    async def stream_chat(self, messages):
        yield "Xin chào bạn.", "happy"


class BurstLLM:
    async def stream_chat(self, messages):
        for index in range(100):
            yield f"Câu {index}.", "happy"


class FailingLLM:
    async def stream_chat(self, messages):
        yield "Câu đầu.", "happy"
        raise RuntimeError("simulated producer failure")


class NativeToolLLM:
    def __init__(self, *, second_round_tool_call=False):
        self.calls = []
        self.second_round_tool_call = second_round_tool_call

    async def stream_turn(
        self,
        messages,
        *,
        tools=None,
        detect_end_intent=True,
        tool_choice=None,
    ):
        self.calls.append({
            "messages": messages,
            "tools": tools or [],
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        if len(self.calls) == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(
                call_id="call-1",
                name="calculate",
                arguments={"expression": "2+3"},
            )
            yield CompletedEvent(finish_reason="tool_calls")
            return

        yield ControlEvent()
        if self.second_round_tool_call:
            yield ToolCallReadyEvent(
                call_id="call-2",
                name="calculate",
                arguments={"expression": "10+1"},
            )
        else:
            yield SpeechSegmentEvent("Kết quả phép tính là 5.", emotion="happy")
        yield CompletedEvent(finish_reason="stop")


class TwoFrameTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        yield b"frame-1"
        yield b"frame-2"


class BlockingTTS:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        self.started.set()
        await self.release.wait()
        if not (cancel_event and cancel_event.is_set()):
            yield b"late-frame"


class NoAudioTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        if False:
            yield b"unused"


class StallingBinaryWebSocket(FakeWebSocket):
    def __init__(self):
        super().__init__()
        self.binary_started = asyncio.Event()
        self.release_binary = asyncio.Event()

    async def send(self, payload):
        if isinstance(payload, bytes):
            self.binary_started.set()
            await self.release_binary.wait()
        self.sent.append(payload)


def decode_text_messages(sent):
    return [json.loads(item) for item in sent if isinstance(item, str)]


class TurnLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, llm=None, tts=None):
        websocket = FakeWebSocket()
        session = SessionForTest(
            websocket,
            AppConfig(),
            tts or TwoFrameTTS(),
            llm or SingleClauseLLM(),
        )
        return session, websocket

    async def test_normal_turn_sends_stop_only_after_last_binary(self):
        session, websocket = self.make_session()
        await session._trigger_ai_turn("Xin chào")
        task = session.current_turn_task
        self.assertIsNotNone(task)
        await asyncio.wait_for(task, timeout=1.0)

        self.assertIsInstance(websocket.sent[-2], bytes)
        stop = json.loads(websocket.sent[-1])
        self.assertEqual(stop["type"], "tts")
        self.assertEqual(stop["state"], "stop")
        self.assertNotIn("interrupt", stop)
        self.assertEqual(session.state, SessionState.LISTENING)
        self.assertGreater(session._playback_guard_until, time.monotonic())

    async def test_full_clause_queue_does_not_deadlock_when_turn_is_cancelled(self):
        tts = BlockingTTS()
        session, _ = self.make_session(llm=BurstLLM(), tts=tts)
        await session._trigger_ai_turn("Bắt đầu")
        task = session.current_turn_task
        await asyncio.wait_for(tts.started.wait(), timeout=1.0)

        session._abort_turn()
        await asyncio.wait_for(task, timeout=1.0)
        self.assertTrue(task.done())

    async def test_client_abort_is_only_stop_after_old_turn_is_cancelled(self):
        tts = BlockingTTS()
        session, websocket = self.make_session(llm=BurstLLM(), tts=tts)
        await session._trigger_ai_turn("Bắt đầu")
        old_task = session.current_turn_task
        await asyncio.wait_for(tts.started.wait(), timeout=1.0)

        await session._handle_text_json(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
        await asyncio.wait_for(old_task, timeout=1.0)

        stops = [
            item for item in decode_text_messages(websocket.sent)
            if item.get("type") == "tts" and item.get("state") == "stop"
        ]
        self.assertEqual(len(stops), 1)
        self.assertEqual(session.state, SessionState.LISTENING)

    async def test_stale_asr_callback_cannot_start_new_turn(self):
        session, websocket = self.make_session()
        session._invalidate_capture()
        self.assertEqual(session._capture_generation, 1)

        await session._on_asr_transcript("câu cũ", True, True, capture_generation=0)
        self.assertEqual(session.processed_transcript, "")
        self.assertIsNone(session.current_turn_task)
        self.assertEqual(websocket.sent, [])

    async def test_realtime_tail_echo_is_guarded_after_server_stop(self):
        session, websocket = self.make_session()
        session.state = SessionState.LISTENING
        session.listening_mode = "realtime"
        session._playback_guard_until = time.monotonic() + 0.2

        await session._on_speech_started(session._capture_generation)
        self.assertTrue(session._discard_asr_until_speech_final)
        self.assertEqual(websocket.sent, [])

    async def test_full_clause_queue_can_finish_normally(self):
        session, websocket = self.make_session(llm=BurstLLM(), tts=NoAudioTTS())
        await session._trigger_ai_turn("Bắt đầu")
        task = session.current_turn_task
        await asyncio.wait_for(task, timeout=1.0)

        messages = decode_text_messages(websocket.sent)
        sentence_starts = [
            item for item in messages
            if item.get("type") == "tts" and item.get("state") == "sentence_start"
        ]
        self.assertEqual(len(sentence_starts), 100)
        self.assertEqual(messages[-1].get("state"), "stop")

    async def test_llm_producer_error_finishes_without_deadlock(self):
        session, websocket = self.make_session(llm=FailingLLM())
        await session._trigger_ai_turn("Bắt đầu")
        task = session.current_turn_task
        await asyncio.wait_for(task, timeout=1.0)

        stops = [
            item for item in decode_text_messages(websocket.sent)
            if item.get("type") == "tts" and item.get("state") == "stop"
        ]
        self.assertEqual(len(stops), 1)
        self.assertEqual(session.state, SessionState.IDLE)

    async def test_network_send_stall_is_cancelled_by_client_abort(self):
        websocket = StallingBinaryWebSocket()
        session = SessionForTest(
            websocket,
            AppConfig(),
            TwoFrameTTS(),
            SingleClauseLLM(),
        )
        await session._trigger_ai_turn("Bắt đầu")
        old_task = session.current_turn_task
        await asyncio.wait_for(websocket.binary_started.wait(), timeout=1.0)

        await session._handle_text_json(json.dumps({"type": "abort", "reason": "wake_word_detected"}))
        await asyncio.wait_for(old_task, timeout=1.0)

        self.assertFalse(any(isinstance(item, bytes) for item in websocket.sent))
        stops = [
            item for item in decode_text_messages(websocket.sent)
            if item.get("type") == "tts" and item.get("state") == "stop"
        ]
        self.assertEqual(len(stops), 1)

    async def test_close_cancels_slow_turn_without_stale_audio(self):
        tts = BlockingTTS()
        session, websocket = self.make_session(tts=tts)
        await session._trigger_ai_turn("Bắt đầu")
        old_task = session.current_turn_task
        await asyncio.wait_for(tts.started.wait(), timeout=1.0)

        await session.close()
        await asyncio.wait_for(old_task, timeout=1.0)
        tts.release.set()
        await asyncio.sleep(0)

        self.assertFalse(session.is_active)
        self.assertFalse(any(isinstance(item, bytes) for item in websocket.sent))

    async def test_two_sessions_keep_turn_ownership_isolated(self):
        session_a, websocket_a = self.make_session()
        session_b, websocket_b = self.make_session()

        await session_a._trigger_ai_turn("A")
        await session_b._trigger_ai_turn("B")
        task_a = session_a.current_turn_task
        task_b = session_b.current_turn_task
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

        self.assertTrue(any(isinstance(item, bytes) for item in websocket_a.sent))
        self.assertTrue(any(isinstance(item, bytes) for item in websocket_b.sent))
        self.assertEqual(session_a.dialogue.messages[-1].role, "assistant")
        self.assertEqual(session_b.dialogue.messages[-1].role, "assistant")

    async def test_tool_result_synthesis_is_explicitly_two_rounds(self):
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        llm = NativeToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, NoAudioTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(llm.calls[0]["tool_choice"])
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        self.assertFalse(llm.calls[1]["detect_end_intent"])
        second_messages = llm.calls[1]["messages"]
        assistant = next(item for item in second_messages if item.get("tool_calls"))
        tool_result = next(item for item in second_messages if item.get("role") == "tool")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
        self.assertEqual(tool_result["tool_call_id"], "call-1")
        self.assertIn('"status":"succeeded"', tool_result["content"])
        self.assertEqual(session.dialogue.messages[-1].content, "Kết quả phép tính là 5.")
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)

    async def test_tool_result_synthesis_never_executes_round_two_tool_call(self):
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        llm = NativeToolLLM(second_round_tool_call=True)
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, NoAudioTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(session.tool_executor._receipts), 1)
        self.assertIn("Kết quả là 5", session.dialogue.messages[-1].content)

    async def test_default_tool_profile_stays_one_round(self):
        llm = NativeToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, AppConfig(), NoAudioTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 1)
        self.assertIn("Kết quả là 5", session.dialogue.messages[-1].content)
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
