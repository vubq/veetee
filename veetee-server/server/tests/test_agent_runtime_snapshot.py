"""Turn-scoped agent binding, snapshot, prompt and model tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from veetee_server.agents import AgentRuntimeSnapshot
from veetee_server.audio import DOWNLINK_PCM_FORMAT, UPLINK_PCM_FORMAT, AudioQueueItem
from veetee_server.audio.codec import FakeOpusDecoder, FakeOpusEncoder
from veetee_server.device_gateway.router import (
    _begin_processing_turn,
    _resolve_session_binding,
)
from veetee_server.domain.session import DeviceSession
from veetee_server.persistence.repository import StoredDevice
from veetee_server.pipeline.asr import FakeASR
from veetee_server.pipeline.factory import _snapshot_model_override
from veetee_server.pipeline.llm import OmniRouteLLMConfig, OmniRouteLLMRuntime
from veetee_server.pipeline.orchestrator import FakePipeline, PipelineOutcome
from veetee_server.pipeline.tts import FakeTTS
from veetee_server.pipeline.vad import FakeVAD
from veetee_server.prompt import AgentPromptProfile


def _snapshot(version: int, *, model_id: str = "groq/openai/gpt-oss-120b") -> AgentRuntimeSnapshot:
    return AgentRuntimeSnapshot(
        agent_id=uuid4(),
        version=version,
        prompt_profile=AgentPromptProfile(
            role_prompt=f"Vai trò phiên bản {version}",
            personality="Điềm tĩnh",
            address_style="Xưng mình và gọi bạn",
            language="vi-VN",
            detail_level="adaptive",
            response_style="tự nhiên",
        ),
        model_id=model_id,
        voice_id="",
        intent_strategy="function_call",
        memory_enabled=True,
        memory_min_confidence=0.8,
        tool_policy={},
        memory_policy={},
    )


class BindingRepository:
    def __init__(self, stored: StoredDevice | None) -> None:
        self.stored = stored
        self.calls: list[tuple[str, str]] = []

    def get_by_device_and_client_id(self, device_id: str, client_id: str) -> StoredDevice | None:
        self.calls.append((device_id, client_id))
        return self.stored


class SnapshotRepository:
    def __init__(self, snapshots: list[AgentRuntimeSnapshot | None]) -> None:
        self.snapshots = snapshots

    def snapshot(self, owner_user_id: Any, agent_id: Any) -> AgentRuntimeSnapshot | None:
        del owner_user_id, agent_id
        return self.snapshots.pop(0)


@pytest.mark.asyncio
async def test_binding_requires_both_device_and_client_identity() -> None:
    owner_id = uuid4()
    agent_id = uuid4()
    stored = StoredDevice(
        uuid4(), owner_id, agent_id, "device-1", "", "", "", "app", "", "client-1",
        None, None, None,
    )
    repository = BindingRepository(stored)
    session = DeviceSession(device_id="device-1", client_id="client-1")

    await _resolve_session_binding(SimpleNamespace(device_repository=repository), session)

    assert repository.calls == [("device-1", "client-1")]
    assert (session.owner_user_id, session.agent_id) == (owner_id, agent_id)

    mismatch = DeviceSession(device_id="device-1", client_id="other-client")
    mismatch_repository = BindingRepository(None)
    await _resolve_session_binding(
        SimpleNamespace(device_repository=mismatch_repository), mismatch
    )
    assert mismatch.owner_user_id is None and mismatch.agent_id is None


@pytest.mark.asyncio
async def test_snapshot_is_fixed_for_current_turn_and_refreshed_next_turn() -> None:
    first = _snapshot(1)
    second = _snapshot(2)
    repository = SnapshotRepository([first, second])
    state = SimpleNamespace(agent_repository=repository)
    session = DeviceSession(
        device_id="device-1", client_id="client-1", owner_user_id=uuid4(), agent_id=uuid4()
    )
    session.accept()

    await session.start_turn()
    await _begin_processing_turn(session, state)
    first_turn = session.current_turn
    assert first_turn is not None and first_turn.snapshot is first

    # A later repository value cannot mutate the snapshot already attached.
    assert first_turn.snapshot.version == 1
    await session.abort_turn()
    await session.start_turn()
    await _begin_processing_turn(session, state)
    assert session.current_turn is not None
    assert session.current_turn.snapshot is second


@pytest.mark.asyncio
async def test_unbound_or_missing_agent_uses_default_snapshot() -> None:
    session = DeviceSession(device_id="device-1", client_id="client-1")
    session.accept()
    await session.start_turn()
    await _begin_processing_turn(session, SimpleNamespace())
    assert session.current_turn is not None and session.current_turn.snapshot is None


@pytest.mark.asyncio
async def test_snapshot_lookup_timeout_falls_back_without_blocking_session() -> None:
    import time

    class SlowRepository:
        def snapshot(self, owner_user_id: Any, agent_id: Any) -> None:
            del owner_user_id, agent_id
            time.sleep(0.05)

    session = DeviceSession(
        device_id="device-1", client_id="client-1", owner_user_id=uuid4(), agent_id=uuid4()
    )
    session.accept()
    await session.start_turn()
    await _begin_processing_turn(
        session, SimpleNamespace(agent_repository=SlowRepository()), timeout_seconds=0.001
    )
    assert session.current_turn is not None and session.current_turn.snapshot is None


class RecordingLLM:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def generate_stream(self, messages: list[Any], **_: Any) -> Any:
        self.messages = messages

        async def stream() -> Any:
            from veetee_server.pipeline.llm import LLMTextDeltaEvent

            yield LLMTextDeltaEvent(delta="Đã rõ.")

        return stream()


class Sink:
    async def emit(self, event: Any) -> None:
        del event


@pytest.mark.asyncio
async def test_pipeline_places_snapshot_profile_inside_system_policy() -> None:
    session = DeviceSession(device_id="device-1", client_id="client-1")
    session.accept()
    turn = await session.start_turn()
    turn.snapshot = _snapshot(3)
    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    speech = (1000).to_bytes(2, "little", signed=True) * 960
    silence = b"\0\0" * 960
    for pcm in (speech, speech, silence, silence):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(pcm), generation=session.ingress_queue.generation
            )
        )
    session.begin_processing()
    llm = RecordingLLM()
    pipeline = FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=1, end_silence_frames=1),
        asr=FakeASR(default_text="Xin chào"),
        llm=llm,
        tts=FakeTTS(chunks_per_sentence=1),
    )

    assert await pipeline.run(session, Sink()) is PipelineOutcome.COMPLETED
    assert llm.messages[0].role == "system"
    assert "PLATFORM POLICY" in llm.messages[0].content
    assert "Vai trò phiên bản 3" in llm.messages[0].content
    assert llm.messages[-1].role == "user" and llm.messages[-1].content == "Xin chào"


@pytest.mark.asyncio
async def test_pipeline_default_turn_still_uses_system_policy() -> None:
    session = DeviceSession(device_id="device-1", client_id="client-1")
    session.accept()
    await session.start_turn()
    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    speech = (1000).to_bytes(2, "little", signed=True) * 960
    silence = b"\0\0" * 960
    for pcm in (speech, speech, silence, silence):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(pcm), generation=session.ingress_queue.generation
            )
        )
    session.begin_processing()
    llm = RecordingLLM()
    pipeline = FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=1, end_silence_frames=1),
        asr=FakeASR(default_text="Mặc định"),
        llm=llm,
        tts=FakeTTS(chunks_per_sentence=1),
    )
    assert await pipeline.run(session, Sink()) is PipelineOutcome.COMPLETED
    assert llm.messages[0].role == "system"
    assert "PLATFORM POLICY" in llm.messages[0].content
    assert "AGENT ROLE" not in llm.messages[0].content


@pytest.mark.asyncio
async def test_model_override_is_turn_scoped_and_shares_runtime_resources() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    runtime = OmniRouteLLMRuntime(
        OmniRouteLLMConfig(api_key="test-key"), http_client=client
    )
    await runtime.startup()
    try:
        default = runtime.create_adapter()
        override = runtime.create_adapter("groq/qwen/qwen3.6-27b")
        assert default.config.model == "groq/openai/gpt-oss-120b"
        assert override.config.model == "groq/qwen/qwen3.6-27b"
        assert default._client is override._client
        assert default.circuit_breaker is override.circuit_breaker
        assert default.semaphore is override.semaphore
    finally:
        await runtime.shutdown()
        await client.aclose()


@pytest.mark.asyncio
async def test_unknown_snapshot_model_falls_back_to_server_default() -> None:
    session = DeviceSession(device_id="device-1", client_id="client-1")
    session.accept()
    await session.start_turn()
    assert session.current_turn is not None
    session.current_turn.snapshot = _snapshot(1, model_id="unknown/model")
    session.begin_processing()
    assert _snapshot_model_override(session) is None
