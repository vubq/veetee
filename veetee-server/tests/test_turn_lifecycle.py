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
    def __init__(self, *, second_round_tool_call=False, second_round_delay=0.0):
        self.calls = []
        self.second_round_tool_call = second_round_tool_call
        self.second_round_delay = second_round_delay

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

        if self.second_round_delay:
            await asyncio.sleep(self.second_round_delay)
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


class NativeTimeToolLLM:
    """Test double simulating AI-authored time synthesis (no direct renderer)."""

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
            "messages": messages,
            "tools": tools or [],
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        if len(self.calls) == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(
                call_id="time-1",
                name="get_current_time",
                arguments={},
            )
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Bây giờ là 07:30, ngày 10/09/2026.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


def _assistant_texts(session):
    return [m.content for m in session.dialogue.messages if m.role == "assistant"]


class NativeChatLLM:
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
            "messages": messages,
            "tools": tools or [],
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        yield ControlEvent()
        yield SpeechSegmentEvent("Xin chào bạn.", emotion="happy")
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


class FirstFrameThenBlockTTS:
    def __init__(self):
        self.first_frame_sent = asyncio.Event()

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        yield b"frame-1"
        self.first_frame_sent.set()
        if cancel_event is not None:
            await cancel_event.wait()


class RecordingTwoTurnLLM:
    def __init__(self):
        self.calls = []

    async def stream_chat(self, messages):
        self.calls.append([dict(item) for item in messages])
        if len(self.calls) == 1:
            yield "ESP32 là một vi điều khiển dùng cho thiết bị nhúng.", "neutral"
        else:
            yield "Mình giải thích kỹ hơn nhé.", "neutral"


class SlowLLM:
    async def stream_chat(self, messages):
        await asyncio.sleep(0.05)
        yield "Phản hồi quá chậm.", "neutral"


class ControlThenSlowLLM:
    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        yield ControlEvent()
        await asyncio.sleep(0.05)
        yield SpeechSegmentEvent("Phản hồi quá chậm.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class CachedFallback:
    class Result:
        text = "Mình đang gặp sự cố tạm thời."
        frames = (b"fallback-frame",)
        provenance = "ai:test-model"

    async def get_recovery(self):
        return self.Result()


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

    async def test_abort_then_new_capture_asr_final_starts_fresh_ai_turn(self):
        tts = FirstFrameThenBlockTTS()
        llm = RecordingTwoTurnLLM()
        session, websocket = self.make_session(llm=llm, tts=tts)

        await session._trigger_ai_turn("Giải thích về ESP32")
        old_task = session.current_turn_task
        await asyncio.wait_for(tts.first_frame_sent.wait(), timeout=1.0)

        await session._handle_text_json(json.dumps({
            "type": "abort",
            "reason": "wake_word_detected",
        }))
        await asyncio.wait_for(old_task, timeout=1.0)

        session.tts_engine = TwoFrameTTS()
        await session._handle_text_json(json.dumps({
            "type": "listen",
            "state": "start",
            "mode": "auto",
        }))
        capture_generation = session._capture_generation
        await session._on_speech_started(capture_generation)
        await session._on_asr_transcript(
            "Giải thích kỹ hơn ý vừa nói",
            True,
            True,
            capture_generation,
        )
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertFalse(session._discard_asr_until_speech_final)
        self.assertEqual(session.state, SessionState.LISTENING)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))

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
        session, websocket = self.make_session(llm=BurstLLM(), tts=TwoFrameTTS())
        session.config.tts.frame_duration_ms = 1
        session.config.tts.send_ahead_ms = 1000
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

    async def test_empty_tts_stream_is_failed_not_completed(self):
        session, _ = self.make_session(tts=NoAudioTTS())
        await session._trigger_ai_turn("Bắt đầu")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "failed")
        self.assertEqual([m.role for m in session.dialogue.messages], ["user"])

    async def test_pre_audio_llm_timeout_uses_cached_vietnamese_fallback(self):
        config = AppConfig()
        config.latency.first_token_timeout_ms = 10
        config.latency.total_turn_timeout_ms = 100
        websocket = FakeWebSocket()
        session = SessionForTest(
            websocket,
            config,
            TwoFrameTTS(),
            SlowLLM(),
            response_audio_cache=CachedFallback(),
        )
        await session._trigger_ai_turn("Bạn nghe rõ không?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertTrue(any(isinstance(item, bytes) for item in websocket.sent))
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "failed")

    async def test_control_event_does_not_satisfy_first_usable_event_timeout(self):
        config = AppConfig()
        config.latency.first_token_timeout_ms = 10
        config.latency.total_turn_timeout_ms = 100
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), ControlThenSlowLLM())

        await session._trigger_ai_turn("Bạn nghe rõ không?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertFalse(any(isinstance(item, bytes) for item in websocket.sent))
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "failed")

    async def test_interrupted_turn_preserves_user_and_sent_partial_for_followup(self):
        tts = FirstFrameThenBlockTTS()
        llm = RecordingTwoTurnLLM()
        session, _ = self.make_session(llm=llm, tts=tts)

        await session._trigger_ai_turn("Giải thích về ESP32")
        first_task = session.current_turn_task
        await asyncio.wait_for(tts.first_frame_sent.wait(), timeout=1.0)
        session._abort_turn()
        await asyncio.wait_for(first_task, timeout=1.0)

        session.tts_engine = TwoFrameTTS()
        await session._trigger_ai_turn("Giải thích kỹ hơn ý vừa nói")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        second_messages = llm.calls[1]
        self.assertTrue(any(
            item["role"] == "user" and item["content"] == "Giải thích về ESP32"
            for item in second_messages
        ))
        self.assertTrue(any(
            item["role"] == "assistant" and "bị ngắt" in item["content"].lower()
            for item in second_messages
        ))

    async def test_same_transcript_is_allowed_in_new_capture(self):
        llm = RecordingTwoTurnLLM()
        session, _ = self.make_session(llm=llm)
        await session._trigger_ai_turn("Nói lại")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        session._invalidate_capture()
        await session._trigger_ai_turn("Nói lại")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(len(llm.calls), 2)

    async def test_llm_producer_error_finishes_without_deadlock(self):
        # Speech is buffered until terminal validation, so a producer failure
        # before CompletedEvent must not have spoken partial speech. With no
        # audio started there is no tts:stop; the turn fails loudly.
        session, websocket = self.make_session(llm=FailingLLM())
        await session._trigger_ai_turn("Bắt đầu")
        task = session.current_turn_task
        await asyncio.wait_for(task, timeout=1.0)

        stops = [
            item for item in decode_text_messages(websocket.sent)
            if item.get("type") == "tts" and item.get("state") == "stop"
        ]
        self.assertEqual(len(stops), 0)
        self.assertFalse(any(isinstance(item, bytes) for item in websocket.sent))
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "failed")
        self.assertEqual(session.state, SessionState.LISTENING)

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
        self.assertTrue(any(m.role == "assistant" for m in session_a.dialogue.messages))
        self.assertTrue(any(m.role == "assistant" for m in session_b.dialogue.messages))

    async def test_tool_result_synthesis_is_explicitly_two_rounds(self):
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        llm = NativeToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(llm.calls[0]["tool_choice"])
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        second_messages = llm.calls[1]["messages"]
        assistant = next(item for item in second_messages if item.get("tool_calls"))
        tool_result = next(item for item in second_messages if item.get("role") == "tool")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-1")
        self.assertEqual(tool_result["tool_call_id"], "call-1")
        self.assertIn('"status":"succeeded"', tool_result["content"])
        self.assertIn("Kết quả phép tính là 5.", _assistant_texts(session))
        # Structured receipt history is kept for the next turn.
        self.assertTrue(any(m.role == "system" and "call-1" in m.content
                            for m in session.dialogue.messages))
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)

    async def test_current_time_receipt_uses_ai_synthesis(self):
        # A01/A02 regression: clock answers are AI-authored from the full
        # receipt envelope, never a direct literal renderer.
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        llm = NativeTimeToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Mấy giờ rồi?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(llm.calls[0]["tool_choice"])
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        self.assertTrue(any("Bây giờ là " in text for text in _assistant_texts(session)))
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)
        self.assertEqual(session.turn_metrics.latest_summary()["tool_calls"], 1)
        # Receipt envelope carries provenance and origin turn.
        receipts = [m.content for m in session.dialogue.messages if m.role == "system"]
        self.assertTrue(any("get_current_time" in content and "origin_turn" in content
                            for content in receipts))

    async def test_current_time_phrasings_leave_tool_selection_to_llm(self):
        for text in ("Mấy giờ rồi?", "Bây giờ là mấy giờ?", "Hôm nay ngày mấy?"):
            with self.subTest(text=text):
                config = AppConfig()
                config.tools.tool_result_synthesis = True
                config.tools.max_llm_rounds_per_turn = 2
                llm = NativeTimeToolLLM()
                session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)

                await session._trigger_ai_turn(text)
                await asyncio.wait_for(session.current_turn_task, timeout=1.0)

                self.assertEqual(len(llm.calls), 2)
                self.assertIsNone(llm.calls[0]["tool_choice"])
                self.assertTrue(any(
                    tool.get("function", {}).get("name") == "get_current_time"
                    for tool in llm.calls[0]["tools"]
                ))
                self.assertEqual(session.turn_metrics.latest_summary()["tool_calls"], 1)
                self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)

    async def test_chat_turn_never_preforces_clock_tool_from_user_text(self):
        llm = NativeChatLLM()
        session = SessionForTest(FakeWebSocket(), AppConfig(), TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Mấy giờ nên đi ngủ để mai dậy sớm?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 1)
        self.assertIsNone(llm.calls[0]["tool_choice"])

    async def test_tool_synthesis_uses_remaining_generation_budget(self):
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        config.latency.first_token_timeout_ms = 100
        config.latency.total_turn_timeout_ms = 50
        llm = NativeToolLLM(second_round_delay=0.03)
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), llm)
        original_execute = session.tool_executor.execute

        async def delayed_execute(*args, **kwargs):
            await asyncio.sleep(0.03)
            return await original_execute(*args, **kwargs)

        session.tool_executor.execute = delayed_execute

        started = time.monotonic()
        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        elapsed = time.monotonic() - started

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "completed")
        self.assertLess(elapsed, 0.2)
        # Tight generation budget times out the synthesis round: no literal
        # success claim is spoken, but the truthful receipt stays in history.
        self.assertFalse(any(
            message.role == "assistant" and "Kết quả" in message.content
            for message in session.dialogue.messages
        ))
        self.assertTrue(any(m.role == "system" and "call-1" in m.content
                            for m in session.dialogue.messages))

    async def test_tool_result_synthesis_never_executes_round_two_tool_call(self):
        # Final synthesis round is speech-only: a late tool call is rejected,
        # never executed, and never phrased as success by a literal renderer.
        config = AppConfig()
        config.tools.tool_result_synthesis = True
        config.tools.max_llm_rounds_per_turn = 2
        llm = NativeToolLLM(second_round_tool_call=True)
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(session.tool_executor._receipts), 1)
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        self.assertEqual(session.turn_metrics.latest_summary()["outcome"], "completed")
        # No false success speech from a literal fallback; the truthful
        # first-round receipt stays in structured history.
        self.assertFalse(any(
            message.role == "assistant" and "Kết quả" in message.content
            for message in session.dialogue.messages
        ))
        self.assertTrue(any(m.role == "system" and "call-1" in m.content
                            for m in session.dialogue.messages))

    async def test_default_tool_profile_uses_two_rounds_for_action(self):
        llm = NativeToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, AppConfig(), TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Hai cộng ba bằng bao nhiêu?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[1]["tool_choice"], "none")
        self.assertIn("Kết quả phép tính là 5.", _assistant_texts(session))
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)

    async def test_normal_chat_stays_one_llm_round(self):
        llm = NativeChatLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, AppConfig(), TwoFrameTTS(), llm)

        await session._trigger_ai_turn("Xin chào")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)

        self.assertEqual(len(llm.calls), 1)
        self.assertIsNone(llm.calls[0]["tool_choice"])
        self.assertIn("Xin chào bạn.", _assistant_texts(session))
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
