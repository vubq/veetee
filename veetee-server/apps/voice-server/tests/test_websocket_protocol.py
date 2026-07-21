from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from veetee_voice_server.app import _valid_device_header
from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.arbiter import ConversationState
from veetee_voice_server.conversation.types import (
    AudioChunk,
    ConversationOutput,
    OutputKind,
    Transcript,
    WakeSource,
)
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.transport.protocol import (
    AbortEvent,
    ProtocolViolationError,
    assistant_sleep_payload,
    llm_payload,
    parse_client_event,
    parse_device_hello,
    server_hello_payload,
    stt_payload,
    tts_payload,
)
from veetee_voice_server.transport.session import VoiceSession, WebSocketConversationSink

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).parents[3] / "packages/contracts/fixtures/ws"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.accepted = asyncio.Event()
        self.text_sent = asyncio.Event()
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed: list[tuple[int, str]] = []
        self.three_audio_frames_sent = asyncio.Event()

    async def accept(self) -> None:
        self.accepted.set()

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)
        self.text_sent.set()

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)
        if len(self.sent_bytes) >= 3:
            self.three_audio_frames_sent.set()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


class FakeVadModel:
    def predict(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        state: np.ndarray[Any, np.dtype[np.float32]],
        context: np.ndarray[Any, np.dtype[np.float32]],
    ) -> tuple[float, np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        return 0.0, state, context


class FakeAsr:
    async def transcribe_pcm(self, *_: object, **__: object) -> Transcript:
        return Transcript("", "vi-VN")


class FakeTts:
    async def synthesize(
        self, *_: object, **__: object
    ) -> AsyncIterator[AudioChunk]:
        if False:
            yield AudioChunk(0, 24_000, "pcm_s16le", b"", final=True)


class FakeEngine:
    async def handle_transcript(self, _: Transcript) -> None:
        return


def session(websocket: FakeWebSocket, settings: Settings) -> VoiceSession:
    return VoiceSession(
        websocket,  # type: ignore[arg-type]
        settings=settings,
        profile=SessionProfile.defaults(settings),
        asr=FakeAsr(),  # type: ignore[arg-type]
        vad_model=FakeVadModel(),  # type: ignore[arg-type]
        tts=FakeTts(),  # type: ignore[arg-type]
        engine_factory=lambda *_: FakeEngine(),  # type: ignore[arg-type,return-value]
    )


async def test_server_waits_for_valid_device_hello_before_replying() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.2
    )
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    task = asyncio.create_task(voice_session.run())
    await websocket.accepted.wait()
    await asyncio.sleep(0)
    assert websocket.sent_text == []

    await websocket.incoming.put(
        {"type": "websocket.receive", "text": fixture("device-hello-v1.json")}
    )
    await websocket.text_sent.wait()
    actual = json.loads(websocket.sent_text[0])
    expected = json.loads(fixture("server-hello-v1.json"))
    expected["session_id"] = voice_session.session_id
    assert actual == expected

    await websocket.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await task


async def test_missing_device_hello_closes_with_protocol_error() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.11
    )
    websocket = FakeWebSocket()
    await session(websocket, settings).run()
    assert websocket.sent_text == []
    assert websocket.closed == [(1002, "device hello timeout")]


async def test_binary_or_invalid_hello_is_rejected_before_server_hello() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.2
    )
    binary_socket = FakeWebSocket()
    await binary_socket.incoming.put(
        {"type": "websocket.receive", "bytes": b"not-a-device-hello"}
    )
    await session(binary_socket, settings).run()
    assert binary_socket.sent_text == []
    assert binary_socket.closed == [(1002, "device hello must be a text frame")]

    invalid_socket = FakeWebSocket()
    invalid = json.loads(fixture("device-hello-v1.json"))
    invalid["unexpected"] = True
    await invalid_socket.incoming.put(
        {"type": "websocket.receive", "text": json.dumps(invalid)}
    )
    await session(invalid_socket, settings).run()
    assert invalid_socket.sent_text == []
    assert invalid_socket.closed == [(1002, "invalid device hello")]


async def test_session_mismatch_closes_after_successful_handshake() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.2
    )
    websocket = FakeWebSocket()
    await websocket.incoming.put(
        {"type": "websocket.receive", "text": fixture("device-hello-v1.json")}
    )
    voice_session = session(websocket, settings)
    task = asyncio.create_task(voice_session.run())
    await websocket.text_sent.wait()
    await websocket.incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "session_id": "different-session",
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "source": "button",
                }
            ),
        }
    )
    await task
    assert websocket.closed == [(1008, "session id mismatch")]


async def test_oversized_opus_packet_closes_with_message_too_big() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.2
    )
    websocket = FakeWebSocket()
    await websocket.incoming.put(
        {"type": "websocket.receive", "text": fixture("device-hello-v1.json")}
    )
    await websocket.incoming.put(
        {"type": "websocket.receive", "bytes": b"x" * 1501}
    )
    await session(websocket, settings).run()
    assert websocket.closed == [(1009, "Opus packet too large")]


async def test_protocol_parser_enforces_size_audio_and_session_contract() -> None:
    hello = fixture("device-hello-v1.json")
    parsed = parse_device_hello(
        hello, expected_sample_rate=16_000, expected_frame_duration=60
    )
    assert parsed.features.mcp is True

    wrong_audio = json.loads(hello)
    wrong_audio["audio_params"]["sample_rate"] = 24_000
    with pytest.raises(ProtocolViolationError, match="uplink sample rate"):
        parse_device_hello(
            json.dumps(wrong_audio),
            expected_sample_rate=16_000,
            expected_frame_duration=60,
        )

    oversized = json.dumps({"type": "hello", "padding": "x" * 8192})
    with pytest.raises(ProtocolViolationError) as oversized_error:
        parse_device_hello(
            oversized, expected_sample_rate=16_000, expected_frame_duration=60
        )
    assert oversized_error.value.close_code == 1009

    with pytest.raises(ProtocolViolationError) as mismatch:
        parse_client_event(
            json.dumps(
                {"session_id": "other", "type": "abort", "reason": "new_turn"}
            ),
            session_id="expected",
        )
    assert mismatch.value.close_code == 1008

    payload = server_hello_payload("session", sample_rate=24_000, frame_duration=60)
    assert payload["audio_params"] == {
        "format": "opus",
        "sample_rate": 24_000,
        "channels": 1,
        "frame_duration": 60,
    }

    fixture_session = "01J00000000000000000000000"
    assert stt_payload(fixture_session, "Xin chào Veetee") == json.loads(
        fixture("stt-final-vietnamese.json")
    )
    assert llm_payload(fixture_session, "thinking") == json.loads(
        fixture("llm-thinking.json")
    )
    assert tts_payload(fixture_session, "start") == json.loads(fixture("tts-start.json"))
    assert tts_payload(fixture_session, "stop") == json.loads(fixture("tts-stop.json"))
    assert assistant_sleep_payload(fixture_session, "inactivity_timeout") == json.loads(
        fixture("system-assistant-sleep-timeout.json")
    )


async def test_device_auth_header_identifier_is_ascii_and_bounded() -> None:
    assert _valid_device_header("aa:bb:cc:dd:ee:ff")
    assert _valid_device_header("2db0f1c7-test")
    assert not _valid_device_header(None)
    assert not _valid_device_header(" id-with-space ")
    assert not _valid_device_header("thiết-bị")


async def test_websocket_sink_wraps_audio_in_one_tts_lifecycle() -> None:
    websocket = FakeWebSocket()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-1",
        output_sample_rate=24_000,
        frame_duration_ms=60,
    )
    try:
        await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
        await sink.emit(
            ConversationOutput(
                OutputKind.AUDIO,
                "turn-1",
                2,
                audio=AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 2_880),
            )
        )
        await sink.emit(ConversationOutput(OutputKind.TTS_STOP, "turn-1", 2))
    finally:
        sink.close()

    controls = [json.loads(item) for item in websocket.sent_text]
    assert controls == [
        {"session_id": "session-1", "type": "tts", "state": "start"},
        {"session_id": "session-1", "type": "tts", "state": "stop"},
    ]
    assert len(websocket.sent_bytes) == 2


async def test_cancelled_generation_stops_paced_audio_before_late_frames() -> None:
    websocket = FakeWebSocket()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-1",
        output_sample_rate=24_000,
        frame_duration_ms=60,
    )
    await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
    audio_task = asyncio.create_task(
        sink.emit(
            ConversationOutput(
                OutputKind.AUDIO,
                "turn-1",
                2,
                audio=AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 14_400),
            )
        )
    )
    await websocket.three_audio_frames_sent.wait()
    sink.mark_cancelled(3)
    await sink.cancel_tts(3)
    await audio_task
    await sink.emit(ConversationOutput(OutputKind.TTS_STOP, "turn-1", 2))
    sink.close()

    assert len(websocket.sent_bytes) == 3
    assert json.loads(websocket.sent_text[-1]) == {
        "session_id": "session-1",
        "type": "tts",
        "state": "stop",
    }


async def test_abort_while_asr_is_pending_keeps_assistant_listening() -> None:
    settings = Settings(
        environment="test", require_device_auth=False, hello_timeout_seconds=0.2
    )
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    await voice_session.inactivity.assistant_opened(WakeSource.BUTTON)

    pending = asyncio.Event()
    asr_task = asyncio.create_task(pending.wait())
    voice_session._asr_task = asr_task
    await voice_session._handle_control(
        AbortEvent(
            session_id=voice_session.session_id,
            type="abort",
            reason="button_interrupt",
            source="button",
        )
    )

    assert asr_task.cancelled()
    assert voice_session.arbiter.snapshot.state is ConversationState.LISTENING
    assert json.loads(websocket.sent_text[-1]) == {
        "session_id": voice_session.session_id,
        "type": "listen",
        "state": "start",
    }
    await voice_session.close()
