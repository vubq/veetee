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
from veetee_voice_server.conversation.types import AudioChunk, Transcript
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.transport.protocol import (
    ProtocolViolationError,
    parse_client_event,
    parse_device_hello,
    server_hello_payload,
)
from veetee_voice_server.transport.session import VoiceSession

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

    async def accept(self) -> None:
        self.accepted.set()

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)
        self.text_sent.set()

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

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


async def test_device_auth_header_identifier_is_ascii_and_bounded() -> None:
    assert _valid_device_header("aa:bb:cc:dd:ee:ff")
    assert _valid_device_header("2db0f1c7-test")
    assert not _valid_device_header(None)
    assert not _valid_device_header(" id-with-space ")
    assert not _valid_device_header("thiết-bị")
