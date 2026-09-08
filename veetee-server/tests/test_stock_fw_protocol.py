import json
import unittest

from config.settings import AppConfig
from core.protocol import (
    ProtocolVersion,
    make_tts_message,
    pack_audio_payload,
    unpack_audio_payload,
)
from core.session import ClientSession, SessionState


class FakeWebSocket:
    request = None

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class FakeASR:
    async def start(self):
        return None

    async def send_audio(self, pcm_bytes):
        return None

    async def finalize(self):
        return None

    async def stop(self):
        return None


class FakeLLM:
    async def stream_chat(self, messages):
        if False:
            yield None


class FakeTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        if False:
            yield b""


class StockSession(ClientSession):
    def _create_asr(self):
        return FakeASR()


class ProtocolPacketTests(unittest.TestCase):
    def test_v1_v2_v3_audio_round_trip(self):
        payload = b"stock-fw-opus"
        for version in (ProtocolVersion.V1, ProtocolVersion.V2, ProtocolVersion.V3):
            with self.subTest(version=version):
                packet = pack_audio_payload(payload, version, timestamp=12345)
                unpacked, timestamp = unpack_audio_payload(packet, version)
                self.assertEqual(unpacked, payload)
                self.assertEqual(timestamp, 12345 if version == ProtocolVersion.V2 else 0)

    def test_truncated_framed_packets_are_dropped(self):
        v2 = pack_audio_payload(b"abcdef", ProtocolVersion.V2)[:-2]
        v3 = pack_audio_payload(b"abcdef", ProtocolVersion.V3)[:-2]
        self.assertEqual(unpack_audio_payload(v2, ProtocolVersion.V2)[0], b"")
        self.assertEqual(unpack_audio_payload(v3, ProtocolVersion.V3)[0], b"")

    def test_stock_tts_message_has_no_private_interrupt_extension(self):
        message = json.loads(make_tts_message("session", "stop"))
        self.assertEqual(message, {"session_id": "session", "type": "tts", "state": "stop"})


class StockFirmwareSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self):
        websocket = FakeWebSocket()
        session = StockSession(websocket, AppConfig(), FakeTTS(), FakeLLM())
        return session, websocket

    async def test_stock_hello_variants_connect_without_private_capabilities(self):
        feature_sets = ({}, {"mcp": True}, {"mcp": True, "aec": True})
        for features in feature_sets:
            with self.subTest(features=features):
                session, websocket = self.make_session()
                await session._handle_text_json(json.dumps({
                    "type": "hello",
                    "version": 1,
                    "features": features,
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                }))
                response = json.loads(websocket.sent[-1])
                self.assertEqual(response["type"], "hello")
                self.assertEqual(session.codec.in_frame_duration_ms, 60)
                self.assertEqual(session.barge_in_policy, "client_only")
                self.assertEqual(session.server_side_aec_requested, features.get("aec") is True)

    async def test_supported_input_frame_duration_is_decoupled_from_tts_output(self):
        session, _ = self.make_session()
        await session._handle_text_json(json.dumps({
            "type": "hello",
            "version": 1,
            "audio_params": {"format": "opus", "frame_duration": 20},
        }))
        self.assertEqual(session.codec.in_frame_duration_ms, 20)
        self.assertEqual(session.codec.in_frame_size, 320)
        self.assertEqual(session.codec.frame_duration_ms, 60)
        self.assertEqual(session.codec.out_frame_size, 1440)

    async def test_unsupported_input_frame_duration_keeps_stock_baseline(self):
        session, _ = self.make_session()
        await session._handle_text_json(json.dumps({
            "type": "hello",
            "version": 1,
            "audio_params": {"format": "opus", "frame_duration": 30},
        }))
        self.assertEqual(session.codec.in_frame_duration_ms, 60)

    async def test_listen_start_interrupts_speaking_with_stock_stop(self):
        for mode in ("auto", "manual", "realtime"):
            with self.subTest(mode=mode):
                session, websocket = self.make_session()
                session.state = SessionState.SPEAKING
                await session._handle_text_json(json.dumps({
                    "type": "listen",
                    "state": "start",
                    "mode": mode,
                }))
                stop = json.loads(websocket.sent[-1])
                self.assertEqual(stop, {
                    "session_id": session.session_id,
                    "type": "tts",
                    "state": "stop",
                })
                self.assertEqual(session.state, SessionState.LISTENING)
                self.assertEqual(session.listening_mode, mode)

    async def test_abort_uses_stock_stop_and_updates_state(self):
        session, websocket = self.make_session()
        session.state = SessionState.SPEAKING
        session.listening_mode = "auto"
        await session._handle_text_json(json.dumps({"type": "abort"}))
        stop = json.loads(websocket.sent[-1])
        self.assertEqual(stop["type"], "tts")
        self.assertEqual(stop["state"], "stop")
        self.assertNotIn("interrupt", stop)
        self.assertEqual(session.state, SessionState.LISTENING)

    async def test_realtime_speech_during_tts_is_echo_guarded(self):
        session, websocket = self.make_session()
        session.state = SessionState.SPEAKING
        session.listening_mode = "realtime"
        await session._on_speech_started()
        self.assertTrue(session._discard_asr_until_speech_final)
        self.assertEqual(session.state, SessionState.SPEAKING)
        self.assertEqual(websocket.sent, [])


if __name__ == "__main__":
    unittest.main()
