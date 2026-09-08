import asyncio
import json
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from config.settings import AppConfig
from core.protocol import ProtocolVersion, unpack_audio_payload
from core.response_audio_cache import ResponseAudioCache
from core.session import ClientSession
from http_server import HttpServer


class FakeASR:
    async def start(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        return None

    async def stop(self):
        return None


class CountingLLM:
    def __init__(self):
        self.chat_calls = 0
        self.greeting_calls = 0
        self.goodbye_calls = 0
        self.end_intent_calls = 0
        self.greeting = "Chào bạn, mình đây."
        self.goodbye = "Ừ, chào bạn nhé. Hẹn gặp lại!"

    async def stream_chat(self, messages):
        self.chat_calls += 1
        yield "LLM response", "neutral"

    async def generate_greetings(self, count=3):
        self.greeting_calls += 1
        return [self.greeting]

    async def generate_goodbye(self, messages, *, reason, user_text=""):
        self.goodbye_calls += 1
        return self.goodbye

    async def classify_end_intent(self, user_text, messages):
        self.end_intent_calls += 1
        return False


class CountingTTS:
    def __init__(self):
        self.calls = 0
        self.voice = "integration-voice"
        self.source_voice = "integration-source"
        self.sample_rate = 24000
        self.frame_duration_ms = 1
        self.denoise = True
        self.temperature = 0.7

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        self.calls += 1
        yield b"opus:" + text.encode("utf-8")


class ConversationWebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = AppConfig()
        config.conversation.enabled = True
        config.conversation.audio_cache_enabled = True
        config.conversation.wake_start_wait_ms = 50
        config.conversation.close_grace_ms = 0
        config.conversation.idle_timeout_seconds = 0
        config.tts.frame_duration_ms = 1
        config.tts.send_ahead_ms = 5

        self.config = config
        self.tts = CountingTTS()
        self.llm = CountingLLM()
        self.cache = ResponseAudioCache(self.tts, config.tts)
        self.active_sessions = {}
        self.http_server = HttpServer(
            config,
            self.active_sessions,
            tts_engine=self.tts,
            llm_engine=self.llm,
            response_audio_cache=self.cache,
        )
        self.asr_patch = patch.object(ClientSession, "_create_asr", lambda _self: FakeASR())
        self.asr_patch.start()
        self.client = TestClient(TestServer(self.http_server.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.cache.shutdown()
        self.asr_patch.stop()

    async def _wait_sessions(self, expected):
        for _ in range(100):
            if len(self.active_sessions) == expected:
                return
            await asyncio.sleep(0.005)
        self.fail(f"active_sessions did not reach {expected}: {self.active_sessions}")

    async def _connect(self, version):
        ws = await self.client.ws_connect("/ws")
        await self._wait_sessions(1)
        await ws.send_json({
            "type": "hello",
            "version": version,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        })
        hello = await asyncio.wait_for(ws.receive_json(), timeout=1)
        self.assertEqual(hello["type"], "hello")
        return ws

    async def _receive_fixed_response(self, ws, version, expected_text):
        received = []
        for _ in range(4):
            message = await asyncio.wait_for(ws.receive(), timeout=1)
            if message.type.name == "TEXT":
                received.append(json.loads(message.data))
            elif message.type.name == "BINARY":
                payload, _ = unpack_audio_payload(message.data, version)
                received.append(payload)
            else:
                self.fail(f"unexpected websocket message type: {message.type}")

        self.assertEqual(received[0]["type"], "tts")
        self.assertEqual(received[0]["state"], "start")
        self.assertEqual(received[1]["type"], "tts")
        self.assertEqual(received[1]["state"], "sentence_start")
        self.assertEqual(received[1]["text"], expected_text)
        self.assertEqual(received[2], b"opus:" + expected_text.encode("utf-8"))
        self.assertEqual(received[3]["type"], "tts")
        self.assertEqual(received[3]["state"], "stop")

    async def _wake_and_receive_greeting(self, ws, version):
        await ws.send_json({"type": "listen", "state": "detect", "text": "VeeTee ơi"})
        await ws.send_json({"type": "listen", "state": "start", "mode": "auto"})
        await self._receive_fixed_response(ws, version, self.llm.greeting)

    async def test_handshake_shared_cache_v1_v2_v3_close_and_reconnect(self):
        for version in (ProtocolVersion.V1, ProtocolVersion.V2, ProtocolVersion.V3):
            with self.subTest(version=version):
                ws = await self._connect(version)
                await self._wake_and_receive_greeting(ws, version)

                self.assertEqual(self.tts.calls, 1)
                self.assertEqual(self.llm.chat_calls, 0)

                if version != ProtocolVersion.V3:
                    await ws.close()
                    await self._wait_sessions(0)
                    continue

                await ws.send_json({"type": "text", "text": "tạm biệt"})
                await self._receive_fixed_response(
                    ws,
                    version,
                    self.llm.goodbye,
                )
                closed = await asyncio.wait_for(ws.receive(), timeout=1)
                self.assertIn(closed.type.name, {"CLOSE", "CLOSED"})
                self.assertEqual(ws.close_code, 1000)
                await self._wait_sessions(0)

        self.assertEqual(self.tts.calls, 2)
        self.assertEqual(self.llm.chat_calls, 0)
        self.assertEqual(self.llm.greeting_calls, 3)
        self.assertEqual(self.llm.goodbye_calls, 1)

    async def test_browser_diagnostics_health_and_ota_endpoints(self):
        diagnostics_response = await self.client.get("/api/diagnostics")
        self.assertEqual(diagnostics_response.status, 200)
        diagnostics = await diagnostics_response.json()
        self.assertTrue(diagnostics["conversation"]["enabled"])
        self.assertEqual(diagnostics["server"]["barge_in_policy"], "client_only")
        self.assertEqual(diagnostics["protocol"]["versions"], [1, 2, 3])
        self.assertEqual(diagnostics["protocol"]["listening_modes"], ["auto", "manual", "realtime"])
        self.assertEqual(diagnostics["protocol"]["browser_input_format"], "pcm16")

        health_response = await self.client.get("/health")
        self.assertEqual(health_response.status, 200)
        health = await health_response.json()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["active_sessions"], 0)

        ota_response = await self.client.get("/ota/")
        self.assertEqual(ota_response.status, 200)
        ota = await ota_response.json()
        self.assertEqual(ota["websocket"]["version"], 1)
        self.assertIn("url", ota["websocket"])


if __name__ == "__main__":
    unittest.main()
