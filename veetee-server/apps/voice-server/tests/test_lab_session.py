from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.conversation.types import (
    AdmissionDisposition,
    AudioChunk,
    ConversationOutput,
    OutputKind,
    Transcript,
)
from veetee_voice_server.manager import LabSessionContext, SessionProfile
from veetee_voice_server.providers.contracts import ToolBroker
from veetee_voice_server.transport.lab import (
    LabConversationSink,
    LabSession,
    SimulatedLabToolBroker,
)

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.outgoing: asyncio.Queue[dict[str, Any] | bytes] = asyncio.Queue()
        self.closed: list[tuple[int, str]] = []

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_text(self, data: str) -> None:
        await self.outgoing.put(json.loads(data))

    async def send_bytes(self, data: bytes) -> None:
        await self.outgoing.put(data)

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
        return Transcript("xin chào", "vi-VN", 0.9, 1.0)


class FakeTts:
    async def synthesize(self, *_: object, **__: object) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 240, final=True)


class EchoEngine:
    def __init__(self, arbiter: TurnArbiter, sink: LabConversationSink) -> None:
        self._arbiter = arbiter
        self._sink = sink

    async def handle_transcript(self, transcript: Transcript) -> AdmissionDisposition:
        context = await self._arbiter.begin_turn(5.0)
        try:
            await self._sink.emit(
                ConversationOutput(
                    OutputKind.ADMISSION,
                    context.turn_id,
                    context.generation,
                    {"disposition": "accepted", "confidence": 0.98},
                )
            )
            await self._sink.emit(
                ConversationOutput(
                    OutputKind.TEXT_DELTA,
                    context.turn_id,
                    context.generation,
                    {"text": f"Đã nhận: {transcript.text}"},
                )
            )
            await self._arbiter.mark_speaking(context)
            await self._sink.emit(
                ConversationOutput(OutputKind.TTS_START, context.turn_id, context.generation)
            )
            await self._sink.emit(
                ConversationOutput(
                    OutputKind.AUDIO,
                    context.turn_id,
                    context.generation,
                    audio=AudioChunk(0, 24_000, "pcm_s16le", b"\0\0" * 240, final=True),
                )
            )
            await self._sink.emit(
                ConversationOutput(OutputKind.TTS_STOP, context.turn_id, context.generation)
            )
            return AdmissionDisposition.ACCEPTED
        finally:
            await self._arbiter.complete_turn(context)


class NoResultEngine:
    async def handle_transcript(self, _: Transcript) -> None:
        return


def make_session(websocket: FakeWebSocket) -> LabSession:
    settings = Settings(
        environment="test",
        first_input_seconds=10,
        between_turns_seconds=10,
        _env_file=None,  # type: ignore[call-arg]
    )
    context = LabSessionContext(
        session_id="11111111-1111-4111-8111-111111111111",
        tenant_id="22222222-2222-4222-8222-222222222222",
        user_id="33333333-3333-4333-8333-333333333333",
        agent_id="44444444-4444-4444-8444-444444444444",
        config_version=3,
        input_mode="text",
        mcp_mode="simulated",
        device_id=None,
    )
    profile = SessionProfile.defaults(settings)

    def engine_factory(
        arbiter: TurnArbiter,
        sink: LabConversationSink,
        _: SessionProfile,
        __: ToolBroker,
    ) -> ConversationEngine:
        return EchoEngine(arbiter, sink)  # type: ignore[return-value]

    return LabSession(
        websocket,  # type: ignore[arg-type]
        settings=settings,
        context=context,
        profile=profile,
        asr=FakeAsr(),  # type: ignore[arg-type]
        vad_model=FakeVadModel(),  # type: ignore[arg-type]
        tts=FakeTts(),  # type: ignore[arg-type]
        tool_broker=SimulatedLabToolBroker(),
        engine_factory=engine_factory,
    )


async def test_text_lab_marks_vad_asr_bypassed_and_streams_real_tts_pcm() -> None:
    websocket = FakeWebSocket()
    session = make_session(websocket)
    task = asyncio.create_task(session.run())
    hello = await websocket.outgoing.get()
    assert isinstance(hello, dict)
    assert hello["type"] == "lab.hello"
    assert hello["fidelity"]["vad_asr"] == "bypassed"
    assert hello["prompt"] == {
        "applied": True,
        "version": 3,
        "language": "Tiếng Việt",
        "personality": "local-default",
    }

    await websocket.outgoing.get()  # session.opened
    await websocket.outgoing.get()  # listen.start
    await websocket.incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(
                {
                    "type": "lab.text",
                    "session_id": session.session_id,
                    "text": "Thời tiết hôm nay thế nào?",
                }
            ),
        }
    )

    names: list[str] = []
    received_audio = False
    while "tts.stop" not in names:
        item = await asyncio.wait_for(websocket.outgoing.get(), timeout=1)
        if isinstance(item, bytes):
            received_audio = True
        else:
            names.append(str(item.get("event")))

    assert names[:3] == ["vad.bypassed", "asr.bypassed", "stt.final"]
    assert "admission.final" in names
    assert "llm.delta" in names
    assert "tts.first_audio" in names
    assert received_audio

    await websocket.incoming.put(
        {
            "type": "websocket.receive",
            "text": json.dumps({"type": "lab.close", "session_id": session.session_id}),
        }
    )
    await task


async def test_simulated_lab_tools_validate_ranges_and_keep_session_state() -> None:
    broker = SimulatedLabToolBroker()
    from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext

    context = OperationContext("lab", "turn", 1, CancellationToken(), 10_000_000_000.0)
    result = await broker.call("self.audio_speaker.set_volume", {"volume": 24}, context)
    status = await broker.call("self.get_device_status", {}, context)

    assert result["volume"] == 24
    assert status["volume"] == 24
    with pytest.raises(ValueError, match="Invalid simulated MCP arguments"):
        await broker.call("self.audio_speaker.set_volume", {"volume": 101}, context)


async def test_provider_failure_rearms_lab_inactivity_timeout() -> None:
    session = make_session(FakeWebSocket())
    session.engine = NoResultEngine()  # type: ignore[assignment]
    candidate_rejected = AsyncMock()

    with patch.object(session.inactivity, "candidate_rejected", candidate_rejected):
        await session._run_transcript(Transcript("xin chào", "vi-VN", 0.99, 1.0))

    candidate_rejected.assert_awaited_once()
    await session.inactivity.close()
