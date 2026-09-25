import asyncio
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from config.settings import AppConfig
from core.management_store import ManagementStore
from core.protocol import ProtocolVersion, unpack_audio_payload
from core.response_audio_cache import ResponseAudioCache
from core.providers.tts.scheduler import TTSPreempted
from core.session import ClientSession
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent
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
        self.calls = []
        self.reply = "Chào bạn, mình đây."
        self.goodbye = "Ừ, chào bạn nhé. Hẹn gặp lại!"
        self.base_prompt = "Bạn là VeeTee."

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({
            "messages": [dict(item) for item in messages],
            "tools": list(tools or []),
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        latest = messages[-1] if messages else {}
        idle_request = any(
            item.get("role") == "system"
            and "hội thoại đã hết thời gian chờ" in str(item.get("content", ""))
            for item in messages
        )
        if idle_request:
            # Idle farewell is intentionally isolated from old dialogue.
            yield SpeechSegmentEvent(self.goodbye, emotion="relaxed")
            yield CompletedEvent(finish_reason="stop")
            return

        latest_user = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        should_end = latest_user == "thôi mình đi ngủ đây"
        yield ControlEvent(
            intent="end_conversation" if should_end else "chat",
            lifecycle="end" if should_end else "continue",
            emotion="relaxed" if should_end else "neutral",
        )
        yield SpeechSegmentEvent(self.goodbye if should_end else self.reply, emotion="neutral")
        yield CompletedEvent(finish_reason="stop")

    def get_base_prompt(self):
        return self.base_prompt

    def set_base_prompt(self, value, persist=True):
        self.base_prompt = value


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

    async def synthesize_wav(self, text):
        self.calls += 1
        return b"RIFF-test-wav"


class ConversationWebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = AppConfig()
        config.management.token = "integration-secret"
        config.conversation.enabled = True
        config.conversation.audio_cache_enabled = True
        config.conversation.wake_start_wait_ms = 50
        config.conversation.close_grace_ms = 0
        config.conversation.idle_timeout_seconds = 0
        config.tts.frame_duration_ms = 1
        config.tts.send_ahead_ms = 5

        self.config = config
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ManagementStore(self.tempdir.name + "/manager-state.json")
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
            management_store=self.store,
        )
        self.asr_patch = patch.object(ClientSession, "_create_asr", lambda _self: FakeASR())
        self.asr_patch.start()
        self.client = TestClient(TestServer(self.http_server.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.cache.shutdown()
        self.asr_patch.stop()
        self.tempdir.cleanup()

    async def _wait_sessions(self, expected):
        for _ in range(200):
            if len(self.active_sessions) == expected:
                return
            await asyncio.sleep(0.005)
        self.fail(f"active_sessions did not reach {expected}: {self.active_sessions}")

    async def _wait_conversation_disarmed(self):
        for _ in range(200):
            if self.active_sessions:
                session = next(iter(self.active_sessions.values()))
                if not session._conversation_armed and session._closing_reason is None:
                    return session
            await asyncio.sleep(0.005)
        self.fail("active session did not return to idle")

    async def _connect(self, version):
        manager_cookie = self.http_server.websocket_access.manager_cookie()
        ws = await self.client.ws_connect(
            "/ws", headers={"Cookie": f"veetee_session={manager_cookie}"}
        )
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

    async def _pair_stock_device(self, *, device_id="aa:bb:cc:dd:ee:01", client_id="client-1", extra_headers=None):
        headers = {
            "Device-Id": device_id,
            "Client-Id": client_id,
            "Activation-Version": "1",
            **(extra_headers or {}),
        }
        first = await self.client.post(
            "/ota/",
            headers=headers,
            json={"application": {"version": "2.0.0"}, "board": {"type": "test-board"}},
        )
        self.assertEqual(first.status, 200)
        first_payload = await first.json()
        self.assertIn("activation", first_payload)
        self.assertNotIn("challenge", first_payload["activation"])
        code = first_payload["activation"]["code"]

        pending = await self.client.post("/ota/activate", headers=headers, json={})
        self.assertEqual(pending.status, 202)

        paired = await self.client.post(
            "/api/devices/pair",
            headers={"X-Veetee-Management-Token": "integration-secret"},
            json={"code": code, "assistant_id": "default", "name": "Integration ESP32"},
        )
        self.assertEqual(paired.status, 200)

        activated = await self.client.post("/ota/activate", headers=headers, json={})
        self.assertEqual(activated.status, 200)

        final = await self.client.post("/ota/", headers=headers, json={})
        self.assertEqual(final.status, 200)
        final_payload = await final.json()
        self.assertIn("websocket", final_payload)
        self.assertTrue(final_payload["websocket"]["token"])
        return headers, final_payload

    async def _receive_ai_response(self, ws, version, expected_text):
        received = []
        while True:
            message = await asyncio.wait_for(ws.receive(), timeout=1)
            if message.type.name == "TEXT":
                item = json.loads(message.data)
                received.append(item)
                if item.get("type") == "tts" and item.get("state") == "stop":
                    break
            elif message.type.name == "BINARY":
                payload, _ = unpack_audio_payload(message.data, version)
                received.append(payload)
            else:
                self.fail(f"unexpected websocket message type: {message.type}")

        llm_status = next(item for item in received if isinstance(item, dict) and item.get("type") == "llm")
        self.assertIn("emotion", llm_status)
        tts_messages = [item for item in received if isinstance(item, dict) and item.get("type") == "tts"]
        self.assertEqual([item.get("state") for item in tts_messages], ["start", "sentence_start", "stop"])
        self.assertEqual(tts_messages[1]["text"], expected_text)
        self.assertIn(b"opus:" + expected_text.encode("utf-8"), received)

    async def _wake_and_receive_ai(self, ws, version):
        await ws.send_json({"type": "listen", "state": "detect", "text": "VeeTee ơi"})
        await ws.send_json({"type": "listen", "state": "start", "mode": "auto"})
        await self._receive_ai_response(ws, version, self.llm.reply)

    async def test_handshake_v1_v2_v3_wake_uses_ai_and_v3_contextual_end_closes(self):
        for version in (ProtocolVersion.V1, ProtocolVersion.V2, ProtocolVersion.V3):
            with self.subTest(version=version):
                ws = await self._connect(version)
                try:
                    before_calls = len(self.llm.calls)
                    await self._wake_and_receive_ai(ws, version)
                    self.assertEqual(len(self.llm.calls), before_calls + 1)
                    latest_user = next(
                        item for item in reversed(self.llm.calls[-1]["messages"])
                        if item.get("role") == "user"
                    )
                    self.assertEqual(latest_user["content"], "VeeTee ơi")

                    if version == ProtocolVersion.V3:
                        # Auto-end is disabled by default in production. This
                        # branch explicitly opts in to keep compatibility mode
                        # covered without giving normal chat authority to close.
                        self.config.intent.semantic_end_enabled = True
                        self.config.conversation.end_intent_ai_enabled = True
                        await ws.send_json({"type": "text", "text": "thôi mình đi ngủ đây"})
                        await self._receive_ai_response(ws, version, self.llm.goodbye)
                        closed = await asyncio.wait_for(ws.receive(), timeout=1)
                        self.assertIn(closed.type.name, {"CLOSE", "CLOSED"})
                        self.assertEqual(ws.close_code, 1000)
                finally:
                    if not ws.closed:
                        await ws.close()
                    await self._wait_sessions(0)

    async def test_idle_timeout_closes_session_after_goodbye(self):
        self.config.conversation.goodbye_enabled = True
        self.config.conversation.idle_timeout_seconds = 0.03

        ws = await self._connect(ProtocolVersion.V1)
        try:
            await self._wake_and_receive_ai(ws, ProtocolVersion.V1)
            # Idle goodbye uses the fixed-response path: tts
            # start/sentence_start/stop, no llm status message.
            got_goodbye = False
            while True:
                message = await asyncio.wait_for(ws.receive(), timeout=5)
                if message.type.name == "TEXT":
                    item = json.loads(message.data)
                    if item.get("type") == "tts" and item.get("state") == "sentence_start":
                        self.assertEqual(item.get("text"), self.llm.goodbye)
                        got_goodbye = True
                    if item.get("type") == "tts" and item.get("state") == "stop":
                        break
                elif message.type.name != "BINARY":
                    self.fail(f"unexpected websocket message type: {message.type}")
            self.assertTrue(got_goodbye)
            closed = await asyncio.wait_for(ws.receive(), timeout=5)
            self.assertIn(closed.type.name, {"CLOSE", "CLOSED"})
            self.assertEqual(ws.close_code, 1000)
            await self._wait_sessions(0)
        finally:
            if not ws.closed:
                await ws.close()

    async def test_browser_diagnostics_health_and_ota_endpoints(self):
        self.config.management.token = "integration-secret"
        headers = {"X-Veetee-Management-Token": "integration-secret"}
        denied_diagnostics = await self.client.get("/api/diagnostics")
        self.assertEqual(denied_diagnostics.status, 401)
        diagnostics_response = await self.client.get("/api/diagnostics", headers=headers)
        self.assertEqual(diagnostics_response.status, 200)
        diagnostics = await diagnostics_response.json()
        self.assertTrue(diagnostics["conversation"]["enabled"])
        self.assertEqual(diagnostics["server"]["barge_in_policy"], "client_only")
        self.assertEqual(diagnostics["protocol"]["versions"], [1, 2, 3])
        self.assertEqual(diagnostics["protocol"]["listening_modes"], ["auto", "manual", "realtime"])
        self.assertEqual(diagnostics["protocol"]["browser_input_format"], "pcm16")
        self.assertIn("semantic_routing", diagnostics["conversation"])

        health_response = await self.client.get("/health")
        self.assertEqual(health_response.status, 200)
        health = await health_response.json()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["active_sessions"], 0)

        _, ota = await self._pair_stock_device()
        self.assertEqual(ota["websocket"]["version"], 1)
        self.assertIn("url", ota["websocket"])

    async def test_ota_only_treats_real_tailscale_or_https_headers_as_public_tls(self):
        _, fake_ts_payload = await self._pair_stock_device(
            device_id="aa:bb:cc:dd:ee:11", client_id="client-11",
            extra_headers={"Host": "not-ts.net.evil.example"},
        )
        self.assertTrue(fake_ts_payload["websocket"]["url"].startswith("ws://"))

        _, fake_https_payload = await self._pair_stock_device(
            device_id="aa:bb:cc:dd:ee:12", client_id="client-12",
            extra_headers={"Host": "example.test", "X-Forwarded-Proto": "nothttps"},
        )
        self.assertTrue(fake_https_payload["websocket"]["url"].startswith("ws://"))

        _, funnel_payload = await self._pair_stock_device(
            device_id="aa:bb:cc:dd:ee:13", client_id="client-13",
            extra_headers={"Host": "veetee.tail52a635.ts.net:443"},
        )
        self.assertEqual(
            funnel_payload["websocket"]["url"],
            "wss://veetee.tail52a635.ts.net:443/ws",
        )

        _, proxied_https_payload = await self._pair_stock_device(
            device_id="aa:bb:cc:dd:ee:14", client_id="client-14",
            extra_headers={"Host": "voice.example.com", "X-Forwarded-Proto": "https"},
        )
        self.assertEqual(
            proxied_https_payload["websocket"]["url"],
            "wss://voice.example.com/ws",
        )

    async def test_management_token_protects_prompt_and_test_voice_without_affecting_stock_ota_ws(self):
        self.config.management.token = "integration-secret"
        headers = {"X-Veetee-Management-Token": "integration-secret"}

        denied_diagnostics = await self.client.get("/api/diagnostics")
        self.assertEqual(denied_diagnostics.status, 401)
        allowed_diagnostics = await self.client.get("/api/diagnostics", headers=headers)
        self.assertEqual(allowed_diagnostics.status, 200)

        denied_prompt = await self.client.get("/api/prompt")
        self.assertEqual(denied_prompt.status, 401)
        allowed_prompt = await self.client.get("/api/prompt", headers=headers)
        self.assertEqual(allowed_prompt.status, 200)

        denied_voice = await self.client.post("/api/test-voice", json={"text": "xin chào"})
        self.assertEqual(denied_voice.status, 401)
        allowed_voice = await self.client.post(
            "/api/test-voice",
            json={"text": "xin chào"},
            headers=headers,
        )
        self.assertEqual(allowed_voice.status, 200)

        device_headers, ota = await self._pair_stock_device(
            device_id="aa:bb:cc:dd:ee:21", client_id="client-21"
        )
        ws_headers = {
            "Authorization": f"Bearer {ota['websocket']['token']}",
            "Device-Id": device_headers["Device-Id"],
            "Client-Id": device_headers["Client-Id"],
        }
        ws = await self.client.ws_connect("/ws", headers=ws_headers)
        await ws.send_json({"type": "hello", "version": 1})
        self.assertEqual((await ws.receive_json())["type"], "hello")
        await ws.close()
        await self._wait_sessions(0)

    async def test_management_endpoints_are_disabled_when_token_is_unset(self):
        self.config.management.token = ""
        denied_diagnostics = await self.client.get("/api/diagnostics")
        denied_prompt = await self.client.get("/api/prompt")
        denied_voice = await self.client.post("/api/test-voice", json={"text": "xin chào"})
        self.assertEqual(denied_diagnostics.status, 401)
        self.assertEqual(denied_prompt.status, 401)
        self.assertEqual(denied_voice.status, 401)

    async def test_test_voice_preemption_is_reported_as_live_priority_conflict(self):
        self.config.management.token = "integration-secret"
        headers = {"X-Veetee-Management-Token": "integration-secret"}
        self.tts.synthesize_wav = AsyncMock(
            side_effect=TTSPreempted("dashboard yielded to live")
        )

        response = await self.client.post(
            "/api/test-voice",
            json={"text": "đọc thử"},
            headers=headers,
        )
        self.assertEqual(response.status, 409)
        payload = await response.json()
        self.assertEqual(payload["code"], "TTS_PREEMPTED")

    async def test_test_voice_rate_limit_is_bounded(self):
        self.config.management.token = "integration-secret"
        self.config.management.test_voice_requests_per_minute = 1
        headers = {"X-Veetee-Management-Token": "integration-secret"}
        first = await self.client.post("/api/test-voice", json={"text": "một"}, headers=headers)
        second = await self.client.post("/api/test-voice", json={"text": "hai"}, headers=headers)
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 429)

    async def test_diagnostics_retains_bounded_turn_trace_after_disconnect(self):
        self.config.management.token = "integration-secret"
        headers = {"X-Veetee-Management-Token": "integration-secret"}
        ws = await self._connect(ProtocolVersion.V1)
        await self._wait_sessions(1)
        session = next(iter(self.active_sessions.values()))
        trace = session.turn_metrics.start_turn(7, "integration")
        trace.mark("review_probe", semantic="continue", token="must-not-leak")
        trace.mark("tts_lock_acquired", queue_wait_ms=10.0)
        trace.mark("tts_lease_held", held_ms=100.0)
        trace.mark("tts_lock_acquired", queue_wait_ms=20.0)
        trace.mark("tts_lease_held", held_ms=200.0)
        trace.mark("first_ws_binary_sent")
        trace.finish("completed", llm_rounds=1, tool_calls=0)
        turn_id = trace.turn_id

        await ws.close()
        await self._wait_sessions(0)

        response = await self.client.get("/api/diagnostics", headers=headers)
        self.assertEqual(response.status, 200)
        diagnostics = await response.json()
        self.assertEqual(diagnostics["runtime"]["recent_turn_count"], 1)
        latest = diagnostics["runtime"]["latest_turns"][-1]
        self.assertEqual(latest["turn_id"], turn_id)
        self.assertEqual(latest["outcome"], "completed")
        self.assertEqual(
            latest["stage_aggregates_ms"]["tts_queue_wait"]["max_ms"],
            20.0,
        )
        self.assertEqual(
            latest["stage_aggregates_ms"]["tts_lease_held"]["total_ms"],
            300.0,
        )

        denied_detail = await self.client.get(f"/api/turns/{turn_id}")
        self.assertEqual(denied_detail.status, 401)
        detail_response = await self.client.get(
            f"/api/turns/{turn_id}",
            headers=headers,
        )
        self.assertEqual(detail_response.status, 200)
        detail = await detail_response.json()
        self.assertEqual(detail["turn_id"], turn_id)
        review_event = next(
            item for item in detail["events"] if item["name"] == "review_probe"
        )
        self.assertEqual(review_event["semantic"], "continue")
        self.assertEqual(review_event["token"], "<redacted>")

        missing = await self.client.get("/api/turns/not-found", headers=headers)
        self.assertEqual(missing.status, 404)


if __name__ == "__main__":
    unittest.main()
