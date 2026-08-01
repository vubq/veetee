from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from time import sleep
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
from veetee_voice_server.conversation.memory import MemoryPolicy
from veetee_voice_server.conversation.types import (
    AudioChunk,
    ConversationOutput,
    OutputKind,
    Transcript,
    WakeSource,
)
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.transport.lab import SimulatedLabToolBroker
from veetee_voice_server.transport.opus import OpusEncoder
from veetee_voice_server.transport.protocol import (
    AbortEvent,
    ListenEvent,
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


class FakeTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(
        self,
        session_id: str,
        event_type: str,
        *,
        generation: int,
        turn_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        del session_id, generation, turn_id
        self.events.append((event_type, payload or {}))

    async def close(self) -> None:
        return


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


class FailingEngine:
    async def handle_transcript(self, _: Transcript) -> None:
        raise RuntimeError("private turn fixture detail")


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


async def test_wake_detect_buffers_binary_until_matching_start_without_reset() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        wake_audio_pre_roll_max_ms=2_000,
    )
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    telemetry = FakeTelemetry()
    voice_session._telemetry = telemetry  # type: ignore[assignment]
    encoder = OpusEncoder(16_000)
    try:
        packet = encoder.encode(b"\0\0" * 960, frame_samples=960)
        await voice_session._handle_control(
            ListenEvent(
                session_id=voice_session.session_id,
                type="listen",
                state="detect",
                source="wake_word",
            )
        )
        await voice_session._handle_audio(packet)

        assert voice_session.arbiter.snapshot.state is ConversationState.STANDBY
        assert len(voice_session._pending_wake_audio) == 960 * 2

        with patch.object(
            voice_session,
            "_replay_pending_wake_audio",
            new=AsyncMock(),
        ) as replay:
            await voice_session._handle_control(
                ListenEvent(
                    session_id=voice_session.session_id,
                    type="listen",
                    state="start",
                    mode="auto",
                    source="wake_word",
                )
            )

        replay.assert_awaited_once()
        assert len(replay.await_args.args[0]) == 960 * 2
        assert voice_session._pending_wake_audio == b""
        assert any(
            event == "wake_audio.preroll_applied"
            and payload["audio_bytes"] == 960 * 2
            for event, payload in telemetry.events
        )
    finally:
        encoder.close()
        await voice_session.close()


async def test_wake_preroll_is_bounded_and_stale_paths_discard_it() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        wake_audio_pre_roll_max_ms=60,
    )
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    encoder = OpusEncoder(16_000)
    try:
        packet = encoder.encode(b"\0\0" * 960, frame_samples=960)
        detect = ListenEvent(
            session_id=voice_session.session_id,
            type="listen",
            state="detect",
            source="wake_word",
        )
        await voice_session._handle_control(detect)
        await voice_session._handle_audio(packet)
        await voice_session._handle_audio(packet)
        assert len(voice_session._pending_wake_audio) == 960 * 2
        assert voice_session._pending_wake_dropped_bytes == 960 * 2

        await voice_session._handle_control(
            AbortEvent(
                session_id=voice_session.session_id,
                type="abort",
                reason="button_interrupt",
                source="button",
            )
        )
        assert voice_session._pending_wake_audio == b""
        assert voice_session._pending_wake_generation is None

        await voice_session._handle_control(detect)
        await voice_session._handle_audio(packet)
        with patch.object(
            voice_session,
            "_replay_pending_wake_audio",
            new=AsyncMock(),
        ) as replay:
            await voice_session._handle_control(
                ListenEvent(
                    session_id=voice_session.session_id,
                    type="listen",
                    state="start",
                    mode="auto",
                    source="button",
                )
            )
        replay.assert_not_awaited()
        assert voice_session._pending_wake_audio == b""

        await voice_session._handle_control(
            ListenEvent(
                session_id=voice_session.session_id,
                type="listen",
                state="stop",
                reason="user_disable",
            )
        )
        assert voice_session.arbiter.snapshot.state is ConversationState.STANDBY
        await voice_session._handle_control(detect)
        await voice_session._handle_audio(packet)
        assert voice_session._pending_wake_audio
        await voice_session.close()
        assert voice_session._pending_wake_audio == b""
        assert voice_session._pending_wake_generation is None
    finally:
        encoder.close()
        await voice_session.close()


async def test_listen_detect_requires_explicit_wake_source_but_not_phrase_text() -> None:
    session_id = "session-1"
    parsed = parse_client_event(
        json.dumps(
            {
                "session_id": session_id,
                "type": "listen",
                "state": "detect",
                "source": "wake_word",
            }
        ),
        session_id=session_id,
    )
    assert isinstance(parsed, ListenEvent)
    assert parsed.text is None

    with pytest.raises(ProtocolViolationError, match="invalid client event"):
        parse_client_event(
            json.dumps(
                {
                    "session_id": session_id,
                    "type": "listen",
                    "state": "detect",
                    "source": "button",
                }
            ),
            session_id=session_id,
        )


async def test_wake_detect_is_rejected_outside_standby_or_closing() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    await voice_session._handle_control(
        ListenEvent(
            session_id=voice_session.session_id,
            type="listen",
            state="start",
            mode="auto",
            source="button",
        )
    )

    with pytest.raises(
        ProtocolViolationError,
        match="wake detect outside standby or closing",
    ) as error:
        await voice_session._handle_control(
            ListenEvent(
                session_id=voice_session.session_id,
                type="listen",
                state="detect",
                source="wake_word",
            )
        )

    assert error.value.close_code == 1008
    assert voice_session._pending_wake_audio == b""
    assert voice_session._pending_wake_generation is None
    await voice_session.close()


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
        policy=replace(profile.policy, closing_grace_seconds=0.11, tts_stream_idle_seconds=0.3),
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
    if isinstance(tts, FailingGoodbyeTts):
        assert llm_payload(
            voice_session.session_id,
            "sad",
            text="goodbye_tts_failed",
        ) in controls
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


async def test_websocket_sink_buffers_a_native_sentence_while_next_batch_synthesizes() -> None:
    websocket = FakeWebSocket()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-native-batch",
        output_sample_rate=24_000,
        frame_duration_ms=60,
        playback_queue_seconds=5.0,
    )
    try:
        await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
        await asyncio.wait_for(
            sink.emit(
                ConversationOutput(
                    OutputKind.AUDIO,
                    "turn-1",
                    2,
                    audio=AudioChunk(
                        0,
                        24_000,
                        "pcm_s16le",
                        b"\0\0" * (24_000 * 4),
                    ),
                )
            ),
            timeout=0.5,
        )
        assert sink._audio_stream is not None
        assert sink._audio_stream.queue.qsize() > 50
        await sink.cancel_tts(3)
    finally:
        sink.close()


async def test_websocket_sink_records_paced_sender_summary() -> None:
    websocket = FakeWebSocket()
    telemetry = FakeTelemetry()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-summary",
        telemetry=telemetry,
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
                audio=AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 5_760),
            )
        )
        await sink.emit(ConversationOutput(OutputKind.TTS_STOP, "turn-1", 2))
    finally:
        sink.close()

    summaries = [
        payload
        for event_type, payload in telemetry.events
        if event_type == "tts.paced_sender_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0]["queue_starvation_count"] == 0
    assert summaries[0]["scheduler_lateness_count"] == 0
    assert summaries[0]["frame_duration_ms"] == 60
    assert isinstance(summaries[0]["queue_low_water_frames"], int)


async def test_websocket_sink_shutdown_consumes_closed_transport_sender() -> None:
    class ClosedTransportWebSocket(FakeWebSocket):
        async def send_bytes(self, data: bytes) -> None:
            del data
            raise RuntimeError("fixture transport closed")

    websocket = ClosedTransportWebSocket()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-closed-transport",
        output_sample_rate=24_000,
        frame_duration_ms=60,
    )
    await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
    stream = sink._audio_stream
    assert stream is not None and stream.task is not None
    await sink.emit(
        ConversationOutput(
            OutputKind.AUDIO,
            "turn-1",
            2,
            audio=AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 2_880),
        )
    )
    await asyncio.sleep(0)
    assert stream.task.done()

    await sink.shutdown()
    await sink.emit(ConversationOutput(OutputKind.TTS_START, "late-turn", 3))

    assert sink._audio_stream is None
    assert websocket.sent_text == [
        json.dumps(
            {"session_id": "session-closed-transport", "type": "tts", "state": "start"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    ]


async def test_paced_sender_separates_queue_starvation_from_scheduler_lateness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket()
    telemetry = FakeTelemetry()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-delays",
        telemetry=telemetry,
        output_sample_rate=24_000,
        frame_duration_ms=20,
    )
    monkeypatch.setattr(sink, "_prebuffer_frames", 1)

    await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
    stream = sink._audio_stream
    assert stream is not None
    await sink._enqueue_audio(stream, b"first")
    await asyncio.sleep(0.05)
    await sink._enqueue_audio(stream, b"starved")
    await asyncio.sleep(0.01)
    await sink._enqueue_audio(stream, None)
    assert stream.task is not None
    await stream.task

    assert stream.starvation_count == 1
    assert stream.starvation_seconds > 0
    assert stream.scheduler_lateness_count == 0


async def test_paced_sender_records_scheduler_lateness_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = FakeWebSocket()
    telemetry = FakeTelemetry()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-late-scheduler",
        telemetry=telemetry,
        output_sample_rate=24_000,
        frame_duration_ms=20,
    )
    monkeypatch.setattr(sink, "_prebuffer_frames", 1)

    await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
    stream = sink._audio_stream
    assert stream is not None
    await sink._enqueue_audio(stream, b"first")
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    stream.queue.put_nowait((b"ready", loop.time()))

    def delay_event_loop() -> None:
        sleep(0.04)

    loop.call_later(0.005, delay_event_loop)
    stream.queue.put_nowait((None, loop.time()))
    assert stream.task is not None
    await stream.task

    assert stream.starvation_count == 0
    assert stream.scheduler_lateness_count == 1
    assert stream.scheduler_lateness_seconds > 0


async def test_cancelled_enqueue_cleans_up_queue_and_wait_tasks() -> None:
    websocket = FakeWebSocket()
    sink = WebSocketConversationSink(
        websocket,  # type: ignore[arg-type]
        session_id="session-queue-cancel",
        output_sample_rate=24_000,
        frame_duration_ms=60,
        playback_queue_seconds=0.18,
    )
    try:
        await sink.emit(ConversationOutput(OutputKind.TTS_START, "turn-1", 2))
        stream = sink._audio_stream
        assert stream is not None and stream.task is not None
        stream.task.cancel()
        await asyncio.gather(stream.task, return_exceptions=True)
        while not stream.queue.full():
            stream.queue.put_nowait(
            (b"queued", asyncio.get_running_loop().time())
        )
        baseline_tasks = asyncio.all_tasks()
        enqueue_task = asyncio.create_task(sink._enqueue_audio(stream, b"blocked"))
        await asyncio.sleep(0)
        assert not enqueue_task.done()

        enqueue_task.cancel()
        await asyncio.gather(enqueue_task, return_exceptions=True)
        await asyncio.sleep(0)

        assert not [
            task
            for task in asyncio.all_tasks()
            if task not in baseline_tasks and not task.done()
        ]
    finally:
        if sink._audio_stream is not None:
            sink._audio_stream.cancelled.set()
            if sink._audio_stream.task is not None:
                sink._audio_stream.task.cancel()
                await asyncio.gather(
                    sink._audio_stream.task, return_exceptions=True
                )
        sink.close()


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


async def test_failed_turn_stop_discards_paced_audio_before_stale_frames() -> None:
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
    await sink.emit(
        ConversationOutput(
            OutputKind.TTS_STOP,
            "turn-1",
            2,
            payload={"cancelled": True},
        )
    )
    await audio_task
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


async def test_device_turn_failure_exposes_stable_error_code_only() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    websocket = FakeWebSocket()
    voice_session = session(websocket, settings)
    voice_session.asr = TranscriptAsr()  # type: ignore[assignment]
    voice_session.engine = FailingEngine()  # type: ignore[assignment]
    await voice_session.inactivity.assistant_opened(WakeSource.BUTTON)

    await voice_session._transcribe(b"\0\0" * 320)

    controls = [json.loads(item) for item in websocket.sent_text]
    error = next(
        item
        for item in controls
        if item.get("type") == "llm" and item.get("emotion") == "sad"
    )
    assert error["text"] == "transcription_or_turn_failed"
    assert "RuntimeError" not in json.dumps(error)
    assert controls[-1] == {
        "session_id": voice_session.session_id,
        "type": "listen",
        "state": "start",
    }
    await voice_session.close()


async def test_explicit_unclear_admission_stays_rejected_without_tool() -> None:
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
        "confidence": 0.8,
        "addressed_to_robot": 0.0,
        "reason_code": "invalid_model_output",
    }
    assert output["plan"]["action"] == "noop"
    assert output["plan"]["response_required"] is False
    assert output["plan"]["tool_call"] is None


async def test_invalid_admission_decision_degrades_to_safe_response_without_tool() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {"decision": "maybe"},
            "dialogue_act": "statement",
            "plan": {"action": "invented_tool"},
        },
        schema,
        "vi-VN",
    )

    assert output["admission"]["decision"] == "accepted"
    assert output["admission"]["reason_code"] == "invalid_model_output"
    assert output["plan"]["action"] == "respond"
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


async def test_semantic_schema_recovers_omitted_safe_plan_action() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": 0.96,
                "addressed_to_robot": 0.96,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "social",
            "plan": {
                "locale": "vi-VN",
                "intent": "conversation.social",
                "response_required": True,
                "response_text": "Tôi vẫn đang nghe đây.",
                "tool_call": None,
            },
        },
        schema,
        "vi-VN",
    )

    assert output["admission"]["decision"] == "accepted"
    assert output["plan"]["action"] == "respond"
    assert output["plan"]["response_text"] == "Tôi vẫn đang nghe đây."


async def test_semantic_schema_repairs_nullable_intent_and_unknown_dialogue_act() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": "0.94",
                "addressed_to_robot": 0.91,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "statement",
            "plan": {
                "action": "respond",
                "locale": "vi-VN",
                "intent": None,
                "response_required": True,
                "response_text": "Tôi nghe đây.",
                "tool_call": None,
            },
        },
        schema,
        "vi-VN",
    )

    assert output["admission"]["decision"] == "accepted"
    assert output["admission"]["confidence"] == 0.94
    assert output["dialogue_act"] == "answer"
    assert output["plan"]["intent"] == ""


async def test_semantic_schema_drops_placeholder_tool_from_regular_response() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": 0.94,
                "addressed_to_robot": 0.91,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "question",
            "plan": {
                "action": "respond",
                "locale": "vi-VN",
                "intent": "date.current",
                "response_required": True,
                "response_text": None,
                "tool_call": {},
            },
        },
        schema,
        "vi-VN",
    )

    assert output["admission"]["decision"] == "accepted"
    assert output["admission"]["reason_code"] == "speech_relevant"
    assert output["plan"]["action"] == "respond"
    assert output["plan"]["tool_call"] is None


async def test_semantic_schema_can_force_regular_response_through_prose_stream() -> None:
    schema = _planner_output_schema(SimulatedLabToolBroker())
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": 0.95,
                "addressed_to_robot": 0.95,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "question",
            "plan": {
                "action": "respond",
                "locale": "vi-VN",
                "intent": "conversation.answer",
                "response_required": True,
                "response_text": "Câu trả lời đã được planner sinh đầy đủ.",
                "tool_call": None,
            },
        },
        schema,
        "vi-VN",
        stream_response=True,
    )

    assert output["plan"]["response_text"] is None
    assert output["plan"]["response_required"] is True


async def test_semantic_schema_bounds_model_proposed_memory_facts_when_opted_in() -> None:
    memory_policy = MemoryPolicy(
        enabled=True,
        consent=True,
        store_facts=True,
        max_fact_characters=64,
        fact_retention_days=10,
    )
    schema = _planner_output_schema(
        SimulatedLabToolBroker(), memory_policy=memory_policy
    )
    output = _validated_planner_output(
        {
            "admission": {
                "decision": "accepted",
                "confidence": 0.95,
                "addressed_to_robot": 0.95,
                "reason_code": "speech_relevant",
            },
            "dialogue_act": "answer",
            "plan": {
                "action": "respond",
                "locale": "vi-VN",
                "intent": "preference.remember",
                "response_required": True,
                "response_text": None,
                "tool_call": None,
                "memory_facts": [
                    {
                        "category": "preference",
                        "key": "drink",
                        "value": "cà phê " * 30,
                        "confidence": 2,
                        "expires_in_days": 999,
                    }
                ],
            },
        },
        schema,
        "vi-VN",
        memory_policy=memory_policy,
    )

    fact = output["plan"]["memory_facts"][0]
    assert len(fact["value"]) == 64
    assert fact["confidence"] == 1.0
    assert fact["expires_in_days"] == 10
