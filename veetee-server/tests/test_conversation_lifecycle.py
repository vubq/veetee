import asyncio
import json
import unittest

from config.settings import AppConfig
from core.session import ClientSession, SessionState
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent


class FakeWebSocket:
    request = None

    def __init__(self):
        self.sent = []
        self.closed = False
        self.close_code = None
        self.close_reason = None

    async def send(self, payload):
        if self.closed:
            return False
        self.sent.append(payload)
        return True

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason


class FakeASR:
    def __init__(self):
        self.invalidated_to = 0
        self.last_word_confidence = 0.2
        self.stop_calls = 0

    async def start(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        self.invalidated_to = capture_generation

    async def stop(self):
        self.stop_calls += 1


class LifecycleSession(ClientSession):
    def _create_asr(self):
        return FakeASR()


class CountingLLM:
    def __init__(self, correction="đã sửa", end_intents=None, idle_decisions=None):
        self.calls = []
        self.correction_calls = 0
        self.correction = correction
        self.end_intents = set(end_intents or [])
        self.idle_decisions = list(idle_decisions or [])
        self.reply = "Mình nghe đây."
        self.goodbye = "Ừ, chào bạn nhé. Hẹn gặp lại!"

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({
            "messages": [dict(item) for item in messages],
            "tools": list(tools or []),
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        latest = messages[-1] if messages else {}
        if latest.get("role") == "system" and "Sự kiện hệ thống: hội thoại không có hoạt động" in str(latest.get("content", "")):
            lifecycle = self.idle_decisions.pop(0) if self.idle_decisions else "continue"
            yield ControlEvent(
                intent="end_conversation" if lifecycle == "end" else "chat",
                lifecycle=lifecycle,
                emotion="relaxed",
            )
            if lifecycle == "end":
                yield SpeechSegmentEvent(self.goodbye, emotion="relaxed")
            yield CompletedEvent(finish_reason="stop")
            return

        latest_user = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        should_end = latest_user in self.end_intents
        yield ControlEvent(
            intent="end_conversation" if should_end else "chat",
            lifecycle="end" if should_end else "continue",
            emotion="relaxed" if should_end else "happy",
        )
        yield SpeechSegmentEvent(self.goodbye if should_end else self.reply)
        yield CompletedEvent(finish_reason="stop")

    async def correct_transcript(self, text):
        self.correction_calls += 1
        return self.correction


class CountingTTS:
    def __init__(self):
        self.calls = 0
        self.texts = []
        self.voice = "test-voice"
        self.source_voice = "test-source"
        self.sample_rate = 24000
        self.frame_duration_ms = 1
        self.denoise = True
        self.temperature = 0.7

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        self.calls += 1
        self.texts.append(text)
        yield f"audio:{text}".encode()


class BlockingTTS(CountingTTS):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        self.calls += 1
        self.texts.append(text)
        self.started.set()
        await self.release.wait()
        if not (cancel_event and cancel_event.is_set()):
            yield f"audio:{text}".encode()


def text_messages(websocket):
    return [json.loads(item) for item in websocket.sent if isinstance(item, str)]


class ConversationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, *, goodbye=True, llm=None, tts=None):
        config = AppConfig()
        config.conversation.enabled = True
        config.conversation.goodbye_enabled = goodbye
        config.conversation.wake_start_wait_ms = 5
        config.conversation.close_grace_ms = 0
        config.conversation.idle_timeout_seconds = 0
        config.tts.frame_duration_ms = 1
        config.tts.send_ahead_ms = 5
        websocket = FakeWebSocket()
        llm = llm or CountingLLM()
        tts = tts or CountingTTS()
        session = LifecycleSession(websocket, config, tts, llm)
        return session, websocket, llm, tts

    async def wait_current_turn(self, session):
        for _ in range(200):
            task = session.current_turn_task
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=1)
                return
            await asyncio.sleep(0.001)
        self.fail("AI turn did not start")

    async def wait_llm_calls(self, llm, expected=1):
        for _ in range(300):
            if len(llm.calls) >= expected:
                return
            await asyncio.sleep(0.001)
        self.fail(f"LLM calls did not reach {expected}")

    async def wait_closed(self, websocket):
        for _ in range(200):
            if websocket.closed:
                return
            await asyncio.sleep(0.002)
        self.fail("websocket did not close")

    async def wait_conversation_disarmed(self, session):
        for _ in range(300):
            if not session._conversation_armed and session._closing_reason is None:
                return
            await asyncio.sleep(0.002)
        self.fail("conversation did not return to idle")

    async def test_wake_detect_text_is_routed_through_ai(self):
        session, websocket, llm, tts = self.make_session()
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi"
        }))
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await self.wait_current_turn(session)

        self.assertEqual(len(llm.calls), 1)
        latest_user = next(item for item in reversed(llm.calls[0]["messages"]) if item.get("role") == "user")
        self.assertEqual(latest_user["content"], "VeeTee ơi")
        self.assertEqual(tts.texts, [llm.reply])
        self.assertFalse(websocket.closed)
        self.assertEqual(session.state, SessionState.LISTENING)

    async def test_duplicate_wake_detect_only_starts_one_ai_turn(self):
        session, _, llm, _ = self.make_session()
        detect = json.dumps({"type": "listen", "state": "detect", "text": "VeeTee ơi"})
        await session._handle_text_json(detect)
        await session._handle_text_json(detect)
        await self.wait_llm_calls(llm)
        self.assertEqual(len(llm.calls), 1)

    async def test_compat_greeting_flag_does_not_swallow_wake_text(self):
        session, _, llm, _ = self.make_session()
        session.config.conversation.greeting_enabled = False
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi"
        }))
        await self.wait_llm_calls(llm)
        self.assertEqual(len(llm.calls), 1)

    async def test_quoted_goodbye_is_chat_when_ai_does_not_end(self):
        session, websocket, llm, _ = self.make_session()
        await session._handle_text_json(json.dumps({"type": "text", "text": "‘tạm biệt’"}))
        await self.wait_current_turn(session)
        self.assertEqual(len(llm.calls), 1)
        self.assertFalse(websocket.closed)

    async def test_explaining_goodbye_word_is_chat_when_ai_does_not_end(self):
        session, websocket, llm, _ = self.make_session()
        await session._handle_text_json(json.dumps({"type": "text", "text": "Giải thích từ tạm biệt"}))
        await self.wait_current_turn(session)
        self.assertEqual(len(llm.calls), 1)
        self.assertFalse(websocket.closed)

    async def test_contextual_goodbye_closes_only_when_ai_emits_end(self):
        phrase = "thôi mình đi ngủ đây"
        llm = CountingLLM(end_intents={phrase})
        session, websocket, _, tts = self.make_session(llm=llm)
        await session._handle_text_json(json.dumps({"type": "text", "text": phrase}))
        await self.wait_closed(websocket)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(tts.texts, [llm.goodbye])
        self.assertEqual(websocket.close_code, 1000)

    async def test_raw_asr_goodbye_has_no_keyword_shortcut(self):
        llm = CountingLLM(correction="không được dùng")
        session, websocket, _, _ = self.make_session(llm=llm)
        await session._on_speech_started(session._capture_generation)
        await session._on_asr_transcript("tạm biệt", True, True, session._capture_generation)
        await self.wait_current_turn(session)

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.correction_calls, 0)
        self.assertFalse(websocket.closed)
        messages = text_messages(websocket)
        self.assertEqual(len([m for m in messages if m.get("type") == "stt" and m.get("is_final")]), 1)
        self.assertEqual(len([m for m in messages if m.get("type") == "vad" and m.get("state") == "speech_ended"]), 1)

    async def test_asr_correction_result_is_still_decided_by_ai(self):
        llm = CountingLLM(correction="tạm biệt")
        session, websocket, _, _ = self.make_session(llm=llm)
        session.config.asr.text_correction_enabled = True
        await session._on_speech_started(session._capture_generation)
        await session._on_asr_transcript("mình nói hơi nhỏ", True, True, session._capture_generation)
        await self.wait_current_turn(session)

        self.assertEqual(llm.correction_calls, 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertFalse(websocket.closed)

    async def test_listen_start_cancels_ai_end_before_close_commit(self):
        phrase = "thôi mình đi ngủ đây"
        llm = CountingLLM(end_intents={phrase})
        tts = BlockingTTS()
        session, websocket, _, _ = self.make_session(llm=llm, tts=tts)
        await session._handle_text_json(json.dumps({"type": "text", "text": phrase}))
        await asyncio.wait_for(tts.started.wait(), timeout=1)
        old_task = session.current_turn_task

        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await asyncio.wait_for(old_task, timeout=1)

        self.assertFalse(websocket.closed)
        self.assertIsNone(session._closing_reason)
        self.assertEqual(session.state, SessionState.LISTENING)

    async def test_idle_continue_rearms_once_then_end_logically_idles(self):
        llm = CountingLLM(idle_decisions=["continue", "end"])
        session, websocket, _, _ = self.make_session(goodbye=False, llm=llm)
        session.config.conversation.idle_timeout_seconds = 0.02
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await self.wait_conversation_disarmed(session)

        idle_calls = [
            call for call in llm.calls
            if call["messages"] and call["messages"][-1].get("role") == "system"
            and "Sự kiện hệ thống: hội thoại không có hoạt động" in str(call["messages"][-1].get("content", ""))
        ]
        self.assertEqual(len(idle_calls), 2)
        self.assertTrue(all(call["tool_choice"] == "none" for call in idle_calls))
        self.assertFalse(websocket.closed)
        self.assertTrue(session.is_active)
        self.assertEqual(session.state, SessionState.IDLE)

        await session._handle_text_json(json.dumps({"type": "text", "text": "xin chào"}))
        await self.wait_current_turn(session)
        self.assertFalse(websocket.closed)

    async def test_idle_timeout_waits_while_session_is_speaking(self):
        llm = CountingLLM(idle_decisions=["end"])
        session, websocket, _, _ = self.make_session(goodbye=False, llm=llm)
        session.config.conversation.idle_timeout_seconds = 0.02
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "realtime"
        }))
        session.state = SessionState.SPEAKING
        await asyncio.sleep(0.04)
        self.assertFalse(websocket.closed)
        self.assertTrue(session._conversation_armed)
        self.assertEqual(len(llm.calls), 0)

        session.state = SessionState.LISTENING
        await self.wait_conversation_disarmed(session)
        self.assertFalse(websocket.closed)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
