import asyncio
import json
import unittest

from config.settings import AppConfig
from core.response_audio_cache import ResponseAudioCache
from core.session import ClientSession, SessionState


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
    def __init__(self, correction="đã sửa", end_intents=None):
        self.chat_calls = 0
        self.correction_calls = 0
        self.greeting_calls = 0
        self.goodbye_calls = 0
        self.end_intent_calls = 0
        self.correction = correction
        self.end_intents = set(end_intents or [])
        self.greetings = ["Chào bạn, mình đây.", "Gọi mình có chuyện gì nè?", "Mình nghe đây."]
        self.goodbye = "Ừ, chào bạn nhé. Hẹn gặp lại!"

    async def stream_chat(self, messages):
        self.chat_calls += 1
        yield "Mình nghe đây.", "happy"

    async def stream_chat_with_control(self, messages):
        self.chat_calls += 1
        latest_user = next(
            (str(message.get("content", "")) for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        should_end = latest_user in self.end_intents
        text = self.goodbye if should_end else "Mình nghe đây."
        yield text, "relaxed" if should_end else "happy", should_end

    async def correct_transcript(self, text):
        self.correction_calls += 1
        return self.correction

    async def generate_greetings(self, count=3):
        self.greeting_calls += 1
        return self.greetings[:count]

    async def generate_goodbye(self, messages, *, reason, user_text=""):
        self.goodbye_calls += 1
        return self.goodbye

    async def classify_end_intent(self, user_text, messages):
        self.end_intent_calls += 1
        return user_text in self.end_intents


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
    def make_session(self, *, greeting=True, goodbye=True, cache=False, llm=None, tts=None):
        config = AppConfig()
        config.conversation.enabled = True
        config.conversation.greeting_enabled = greeting
        config.conversation.goodbye_enabled = goodbye
        config.conversation.audio_cache_enabled = cache
        config.conversation.greeting_pool_size = 1
        config.conversation.wake_start_wait_ms = 5
        config.conversation.close_grace_ms = 0
        config.conversation.idle_timeout_seconds = 0
        config.tts.frame_duration_ms = 1
        config.tts.send_ahead_ms = 5
        websocket = FakeWebSocket()
        llm = llm or CountingLLM()
        tts = tts or CountingTTS()
        response_cache = ResponseAudioCache(tts, config.tts) if cache else None
        session = LifecycleSession(websocket, config, tts, llm, response_cache)
        return session, websocket, llm, tts, response_cache

    async def wait_current_turn(self, session):
        for _ in range(100):
            task = session.current_turn_task
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=1)
                return
            await asyncio.sleep(0.001)

    async def wait_closed(self, websocket):
        for _ in range(100):
            if websocket.closed:
                return
            await asyncio.sleep(0.002)
        self.fail("websocket did not close")

    async def test_wake_then_fast_listen_start_uses_ai_greeting_without_chat_turn(self):
        session, websocket, llm, tts, _ = self.make_session()
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi"
        }))
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await self.wait_current_turn(session)

        states = [m.get("state") for m in text_messages(websocket) if m.get("type") == "tts"]
        self.assertEqual(states, ["start", "sentence_start", "stop"])
        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(llm.greeting_calls, 1)
        self.assertEqual(tts.texts, [llm.greetings[0]])
        self.assertEqual(tts.calls, 1)
        self.assertEqual(session.state, SessionState.LISTENING)

    async def test_wake_without_listen_start_uses_bounded_fallback_and_dedupes(self):
        session, _, llm, tts, _ = self.make_session()
        detect = json.dumps({"type": "listen", "state": "detect", "text": "VeeTee ơi"})
        await session._handle_text_json(detect)
        await session._handle_text_json(detect)
        await asyncio.sleep(0.01)
        await self.wait_current_turn(session)

        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(llm.greeting_calls, 1)
        self.assertEqual(tts.calls, 1)

    async def test_greeting_disabled_does_not_start_fake_tts(self):
        session, websocket, llm, tts, _ = self.make_session(greeting=False)
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi"
        }))
        await asyncio.sleep(0.01)

        self.assertIsNone(session.current_turn_task)
        self.assertEqual(websocket.sent, [])
        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(tts.calls, 0)

    async def test_non_wake_detect_remains_normal_chat(self):
        session, websocket, llm, _, _ = self.make_session()
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi, mấy giờ rồi?"
        }))
        await self.wait_current_turn(session)

        self.assertEqual(llm.chat_calls, 1)
        self.assertFalse(websocket.closed)

    async def test_cache_hit_skips_new_tts_synthesis_and_llm(self):
        session, _, llm, tts, cache = self.make_session(cache=True)
        await cache.get_or_fill(llm.greetings[0], 1)
        self.assertEqual(tts.calls, 1)

        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "detect", "text": "VeeTee ơi"
        }))
        await asyncio.sleep(0.01)
        await self.wait_current_turn(session)

        self.assertEqual(tts.calls, 1)
        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(llm.greeting_calls, 1)
        await cache.shutdown()

    async def test_explicit_exit_bypasses_end_classifier_and_uses_ai_goodbye(self):
        session, websocket, llm, tts, _ = self.make_session()
        await session._handle_text_json(json.dumps({"type": "text", "text": "Tạm biệt!"}))
        await self.wait_closed(websocket)

        self.assertEqual(websocket.close_code, 1000)
        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(llm.end_intent_calls, 0)
        self.assertEqual(llm.goodbye_calls, 1)
        self.assertEqual(tts.texts, [llm.goodbye])
        self.assertFalse(session.is_active)
        await session.close()
        self.assertEqual(session.asr.stop_calls, 1)

    async def test_asr_raw_exit_is_checked_before_llm_correction(self):
        llm = CountingLLM(correction="không được dùng")
        session, websocket, _, _, _ = self.make_session(llm=llm)
        await session._on_speech_started(session._capture_generation)
        await session._on_asr_transcript("tạm biệt", True, True, session._capture_generation)
        await self.wait_closed(websocket)

        self.assertEqual(llm.correction_calls, 0)
        self.assertEqual(llm.chat_calls, 0)
        self.assertEqual(llm.end_intent_calls, 0)
        messages = text_messages(websocket)
        self.assertEqual(len([m for m in messages if m.get("type") == "stt" and m.get("is_final")]), 1)
        self.assertEqual(len([m for m in messages if m.get("type") == "vad" and m.get("state") == "speech_ended"]), 1)

    async def test_correction_cannot_turn_ordinary_raw_text_into_exit(self):
        llm = CountingLLM(correction="tạm biệt")
        session, websocket, _, _, _ = self.make_session(llm=llm)
        session.config.asr.text_correction_enabled = True
        await session._on_speech_started(session._capture_generation)
        await session._on_asr_transcript(
            "mình nói hơi nhỏ", True, True, session._capture_generation
        )
        await self.wait_current_turn(session)

        self.assertEqual(llm.correction_calls, 1)
        self.assertEqual(llm.chat_calls, 1)
        self.assertFalse(websocket.closed)

    async def test_ai_end_intent_closes_contextual_phrase(self):
        phrase = "thôi mình đi ngủ đây"
        llm = CountingLLM(end_intents={phrase})
        session, websocket, _, tts, _ = self.make_session(llm=llm)

        await session._handle_text_json(json.dumps({"type": "text", "text": phrase}))
        await self.wait_closed(websocket)

        self.assertEqual(llm.end_intent_calls, 0)
        self.assertEqual(llm.chat_calls, 1)
        self.assertEqual(llm.goodbye_calls, 0)
        self.assertEqual(tts.texts, [llm.goodbye])

    async def test_ai_end_intent_does_not_close_when_goodbye_is_only_mentioned(self):
        llm = CountingLLM()
        session, websocket, _, _, _ = self.make_session(llm=llm)

        await session._handle_text_json(json.dumps({
            "type": "text", "text": "Giải thích từ tạm biệt"
        }))
        await self.wait_current_turn(session)

        self.assertEqual(llm.end_intent_calls, 0)
        self.assertEqual(llm.chat_calls, 1)
        self.assertFalse(websocket.closed)

    async def test_listen_start_cancels_goodbye_before_close_commit(self):
        tts = BlockingTTS()
        session, websocket, llm, _, _ = self.make_session(tts=tts)
        await session._handle_text_json(json.dumps({"type": "text", "text": "tạm biệt"}))
        await asyncio.wait_for(tts.started.wait(), timeout=1)
        old_task = session.current_turn_task

        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await asyncio.wait_for(old_task, timeout=1)

        self.assertFalse(websocket.closed)
        self.assertIsNone(session._closing_reason)
        self.assertEqual(session.state, SessionState.LISTENING)
        self.assertEqual(llm.chat_calls, 0)

    async def test_idle_timeout_needs_no_mic_frames_and_ping_does_not_reset_it(self):
        session, websocket, _, _, _ = self.make_session(goodbye=False)
        session.config.conversation.idle_timeout_seconds = 0.03
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "auto"
        }))
        await asyncio.sleep(0.015)
        await session._handle_text_json(json.dumps({"type": "ping"}))
        await self.wait_closed(websocket)

        self.assertEqual(websocket.close_code, 1000)
        self.assertEqual(websocket.close_reason, "idle_timeout")

    async def test_idle_timeout_waits_while_session_is_speaking(self):
        session, websocket, _, _, _ = self.make_session(goodbye=False)
        session.config.conversation.idle_timeout_seconds = 0.02
        await session._handle_text_json(json.dumps({
            "type": "listen", "state": "start", "mode": "realtime"
        }))
        session.state = SessionState.SPEAKING
        await asyncio.sleep(0.04)
        self.assertFalse(websocket.closed)

        session.state = SessionState.LISTENING
        await self.wait_closed(websocket)
        self.assertEqual(websocket.close_reason, "idle_timeout")


if __name__ == "__main__":
    unittest.main()
