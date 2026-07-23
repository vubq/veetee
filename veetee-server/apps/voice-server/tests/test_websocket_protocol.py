from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from veetee_voice_server.app import (
    _planner_output_schema,
    _valid_device_header,
    _validated_planner_output,
)
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
from veetee_voice_server.transport.lab import SimulatedLabToolBroker
from veetee_voice_server.transport.protocol import (
    AbortEvent,
    ProtocolViolationError,
    assistant_sleep_payload,
    llm_payload,
    mcp_payload,
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
        self.outgoing_text: asyncio.Queue[str] = asyncio.Queue()
        self.sent_bytes: list[bytes] = []
        self.closed: list[tuple[int, str]] = []
        self.three_audio_frames_sent = asyncio.Event()

    async def accept(self) -> None:
        self.accepted.set()

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_text(self, data: str) -> None:
        self.sent_text.append(data)
        await self.outgoing_text.put(data)
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


class TranscriptAsr:
    async def transcribe_pcm(self, *_: object, **__: object) -> Transcript:
        return Transcript("xin chào", "vi-VN", 0.99, 1.0)


class FakeTts:
    async def synthesize(self, *_: object, **__: object) -> AsyncIterator[AudioChunk]:
        if False:
            yield AudioChunk(0, 24_000, "pcm_s16le", b"", final=True)


class SlowGoodbyeTts:
    async def synthesize(self, *_: object, **__: object) -> AsyncIterator[AudioChunk]:
        await asyncio.sleep(0.15)
        yield AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 2_880, final=True)


class FailingGoodbyeTts:
    async def synthesize(self, *_: object, **__: object) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 2_880)
        raise RuntimeError("fixture goodbye failure")


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
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
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
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.11)
    websocket = FakeWebSocket()
    await session(websocket, settings).run()
    assert websocket.sent_text == []
    assert websocket.closed == [(1002, "device hello timeout")]


async def test_binary_or_invalid_hello_is_rejected_before_server_hello() -> None:
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
    binary_socket = FakeWebSocket()
    await binary_socket.incoming.put({"type": "websocket.receive", "bytes": b"not-a-device-hello"})
    await session(binary_socket, settings).run()
    assert binary_socket.sent_text == []
    assert binary_socket.closed == [(1002, "device hello must be a text frame")]

    invalid_socket = FakeWebSocket()
    invalid = json.loads(fixture("device-hello-v1.json"))
    invalid["unexpected"] = True
    await invalid_socket.incoming.put({"type": "websocket.receive", "text": json.dumps(invalid)})
    await session(invalid_socket, settings).run()
    assert invalid_socket.sent_text == []
    assert invalid_socket.closed == [(1002, "invalid device hello")]


async def test_session_mismatch_closes_after_successful_handshake() -> None:
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
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
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
    websocket = FakeWebSocket()
    await websocket.incoming.put(
        {"type": "websocket.receive", "text": fixture("device-hello-v1.json")}
    )
    await websocket.incoming.put({"type": "websocket.receive", "bytes": b"x" * 1501})
    await session(websocket, settings).run()
    assert websocket.closed == [(1009, "Opus packet too large")]

async def test_unlimited_utterance_capture_still_has_a_pcm_memory_bound() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        max_utterance_seconds=0,
        max_utterance_buffer_bytes=1_024 * 1_024,
    )
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    voice_session._speech.extend(b"x" * (settings.max_utterance_buffer_bytes - 1))

    assert voice_session._append_speech(b"yz") is False
    assert len(voice_session._speech) == settings.max_utterance_buffer_bytes

    await voice_session.close()


async def test_protocol_parser_enforces_size_audio_and_session_contract() -> None:
    hello = fixture("device-hello-v1.json")
    parsed = parse_device_hello(hello, expected_sample_rate=16_000, expected_frame_duration=60)
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
        parse_device_hello(oversized, expected_sample_rate=16_000, expected_frame_duration=60)
    assert oversized_error.value.close_code == 1009

    with pytest.raises(ProtocolViolationError) as mismatch:
        parse_client_event(
            json.dumps({"session_id": "other", "type": "abort", "reason": "new_turn"}),
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
    assert llm_payload(fixture_session, "thinking") == json.loads(fixture("llm-thinking.json"))
    assert tts_payload(fixture_session, "start") == json.loads(fixture("tts-start.json"))
    assert tts_payload(fixture_session, "stop") == json.loads(fixture("tts-stop.json"))
    assert assistant_sleep_payload(fixture_session, "inactivity_timeout") == json.loads(
        fixture("system-assistant-sleep-timeout.json")
    )
    assert mcp_payload(
        fixture_session,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"capabilities": {}},
        },
    ) == json.loads((FIXTURES.parent / "mcp/initialize.json").read_text(encoding="utf-8"))


async def test_session_bootstraps_device_mcp_catalog_after_hello() -> None:
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
    websocket = FakeWebSocket()
    captured: dict[str, Any] = {}

    def engine_factory(*args: Any) -> FakeEngine:
        captured["tools"] = args[3]
        return FakeEngine()

    voice_session = VoiceSession(
        websocket,  # type: ignore[arg-type]
        settings=settings,
        profile=SessionProfile.defaults(settings),
        asr=FakeAsr(),  # type: ignore[arg-type]
        vad_model=FakeVadModel(),  # type: ignore[arg-type]
        tts=FakeTts(),  # type: ignore[arg-type]
        engine_factory=engine_factory,  # type: ignore[arg-type]
    )
    await websocket.incoming.put(
        {"type": "websocket.receive", "text": fixture("device-hello-v1.json")}
    )
    task = asyncio.create_task(voice_session.run())

    server_hello = json.loads(await websocket.outgoing_text.get())
    assert server_hello["type"] == "hello"
    initialize = json.loads(await websocket.outgoing_text.get())
    await websocket.incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                mcp_payload(
                    voice_session.session_id,
                    {
                        "jsonrpc": "2.0",
                        "id": initialize["payload"]["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": "veetee-s3-n16r8",
                                "version": "test",
                            },
                        },
                    },
                )
            ),
        }
    )
    tools_list = json.loads(await websocket.outgoing_text.get())
    await websocket.incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                mcp_payload(
                    voice_session.session_id,
                    {
                        "jsonrpc": "2.0",
                        "id": tools_list["payload"]["id"],
                        "result": {
                            "tools": [
                                {
                                    "name": "self.get_device_status",
                                    "description": "Read device state.",
                                    "inputSchema": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {},
                                    },
                                }
                            ],
                            "nextCursor": "",
                        },
                    },
                )
            ),
        }
    )
    assert voice_session._mcp_bootstrap_task is not None
    await voice_session._mcp_bootstrap_task
    assert voice_session.mcp_ready.is_set()
    assert captured["tools"].list_tools()[0]["name"] == "self.get_device_status"

    await websocket.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await task


async def test_device_auth_header_identifier_is_ascii_and_bounded() -> None:
    assert _valid_device_header("aa:bb:cc:dd:ee:ff")
    assert _valid_device_header("2db0f1c7-test")
    assert not _valid_device_header(None)
    assert not _valid_device_header(" id-with-space ")
    assert not _valid_device_header("thiết-bị")


@pytest.mark.parametrize("tts", [SlowGoodbyeTts(), FailingGoodbyeTts()])
async def test_goodbye_always_emits_sleep_when_tts_is_slow_or_fails(tts: object) -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        closing_grace_seconds=0.11,
    )
    websocket = FakeWebSocket()
    profile = SessionProfile.defaults(settings)
    profile = replace(
        profile,
        policy=replace(profile.policy, closing_grace_seconds=0.11, tts_seconds=0.3),
    )
    voice_session = VoiceSession(
        websocket,  # type: ignore[arg-type]
        settings=settings,
        profile=profile,
        asr=FakeAsr(),  # type: ignore[arg-type]
        vad_model=FakeVadModel(),  # type: ignore[arg-type]
        tts=tts,  # type: ignore[arg-type]
        engine_factory=lambda *_: FakeEngine(),  # type: ignore[arg-type,return-value]
    )

    await voice_session._goodbye("first_input_timeout")
    controls = [json.loads(item) for item in websocket.sent_text]

    assert controls[-1] == assistant_sleep_payload(
        voice_session.session_id, "first_input_timeout"
    )
    assert {event.get("state") for event in controls if event.get("type") == "tts"} == {
        "start",
        "stop",
    }
    await voice_session.close()


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
    settings = Settings(environment="test", require_device_auth=False, hello_timeout_seconds=0.2)
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


async def test_provider_failure_rearms_inactivity_after_candidate_started() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    voice_session.asr = TranscriptAsr()  # type: ignore[assignment]
    candidate_rejected = AsyncMock()

    with patch.object(voice_session.inactivity, "candidate_rejected", candidate_rejected):
        await voice_session._transcribe(b"\0\0" * 320)

    candidate_rejected.assert_awaited_once()
    await voice_session.close()


async def test_invalid_semantic_schema_falls_back_to_safe_unclear_without_tool() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "unclear",
                "confidence": 0.8,
                "addressed_to_robot": False,
                "reason_code": "unintelligible_transcript",
            },
            "dialogue_act": "answer",
            "plan": {"action": "invented_tool"},
        },
        schema,
        "vi-VN",
    )

    assert output["admission"] == {
        "decision": "unclear",
        "confidence": 0.0,
        "addressed_to_robot": 0.0,
        "reason_code": "invalid_model_output",
    }
    assert output["plan"]["action"] == "noop"
    assert output["plan"]["tool_call"] is None


async def test_semantic_schema_normalizes_boolean_addressed_signal() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": 0.96,
                "addressed_to_robot": True,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "question",
            "plan": {
                "action": "respond",
                "locale": "vi-VN",
                "intent": "date.current",
                "response_required": True,
                "response_text": "Hôm nay là thứ Tư.",
                "tool_call": None,
            },
        },
        schema,
        "vi-VN",
    )

    assert output["admission"]["addressed_to_robot"] == 1.0
    assert output["admission"]["decision"] == "accepted"
    assert output["plan"]["action"] == "respond"
