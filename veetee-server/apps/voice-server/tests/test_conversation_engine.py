from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from veetee_voice_server.conversation.arbiter import ConversationState, TurnArbiter
from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AdmissionDisposition,
    AudioChunk,
    ConversationPlan,
    ConversationPolicy,
    DialogueAct,
    OutputKind,
    PlanAction,
    Transcript,
    WakeSource,
)
from veetee_voice_server.providers.contracts import LlmRequest, LlmTextDelta
from veetee_voice_server.transport.sink import MemoryConversationSink

pytestmark = pytest.mark.asyncio


class FakeAdmission:
    def __init__(self, disposition: AdmissionDisposition) -> None:
        self.disposition = disposition
        self.calls = 0

    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        self.calls += 1
        context.checkpoint()
        return AdmissionDecision(self.disposition, 0.95, "fixture")


class FakePlanner:
    def __init__(self, plan: ConversationPlan) -> None:
        self.plan_value = plan
        self.calls = 0

    async def plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        context: OperationContext,
    ) -> ConversationPlan:
        self.calls += 1
        context.checkpoint()
        return self.plan_value


class FakeLlm:
    def __init__(self, deltas: tuple[str, ...] = ("Xin chao. ", "Toi co the giup ban.")) -> None:
        self.deltas = deltas
        self.calls = 0
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def stream(
        self, request: LlmRequest, context: OperationContext
    ) -> AsyncIterator[LlmTextDelta]:
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        for delta in self.deltas:
            yield LlmTextDelta(delta)


class FakeTts:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append(text)
        yield AudioChunk(0, 24000, "pcm_s16le", text.encode(), final=True)


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any], context: OperationContext) -> Any:
        self.calls.append((name, arguments))
        return {"ok": True}


def response_plan() -> ConversationPlan:
    return ConversationPlan(
        action=PlanAction.RESPOND,
        dialogue_act=DialogueAct.QUESTION,
        locale="vi-VN",
        intent="fixture.question",
        response_required=True,
    )


def create_engine(
    disposition: AdmissionDisposition = AdmissionDisposition.ACCEPTED,
) -> tuple[
    ConversationEngine,
    TurnArbiter,
    FakeAdmission,
    FakePlanner,
    FakeLlm,
    FakeTts,
    MemoryConversationSink,
]:
    arbiter = TurnArbiter("session-1")
    admission = FakeAdmission(disposition)
    planner = FakePlanner(response_plan())
    llm = FakeLlm()
    tts = FakeTts()
    sink = MemoryConversationSink()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=admission,
        planner=planner,
        llm=llm,
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(sentence_min_characters=1),
    )
    return engine, arbiter, admission, planner, llm, tts, sink


async def test_auto_conversation_replies_without_a_second_button_press() -> None:
    engine, arbiter, _, planner, llm, tts, sink = create_engine()
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Xin chao", "vi-VN", confidence=0.99))

    assert planner.calls == 1
    assert llm.calls == 1
    assert tts.calls
    assert any(output.kind is OutputKind.AUDIO for output in sink.outputs)
    assert [output.kind for output in sink.outputs].count(OutputKind.TTS_START) == 1
    assert [output.kind for output in sink.outputs].count(OutputKind.TTS_STOP) == 1
    assert next(
        index for index, output in enumerate(sink.outputs)
        if output.kind is OutputKind.TTS_START
    ) < next(
        index for index, output in enumerate(sink.outputs)
        if output.kind is OutputKind.AUDIO
    )
    assert arbiter.snapshot.state is ConversationState.LISTENING


@pytest.mark.parametrize(
    "disposition",
    [
        AdmissionDisposition.NON_ACTIONABLE,
        AdmissionDisposition.NOT_ADDRESSED,
        AdmissionDisposition.UNCLEAR,
    ],
)
async def test_rejected_input_never_calls_planner_llm_or_tts(
    disposition: AdmissionDisposition,
) -> None:
    engine, arbiter, _, planner, llm, tts, sink = create_engine(disposition)
    await arbiter.open_assistant(WakeSource.WAKE_WORD)

    await engine.handle_transcript(Transcript("fixture", "vi-VN"))

    assert planner.calls == 0
    assert llm.calls == 0
    assert tts.calls == []
    assert [item.kind for item in sink.outputs] == [OutputKind.ADMISSION]
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_button_abort_drops_late_llm_and_audio_output() -> None:
    engine, arbiter, _, _, llm, tts, sink = create_engine()
    llm.release = asyncio.Event()
    await arbiter.open_assistant(WakeSource.BUTTON)
    task = asyncio.create_task(
        engine.handle_transcript(Transcript("fixture", "vi-VN", confidence=0.99))
    )
    await llm.started.wait()

    receipt = await arbiter.abort("button_interrupt")
    await arbiter.finish_cancellation(receipt)
    llm.release.set()
    await task

    assert tts.calls == []
    assert not any(output.kind is OutputKind.AUDIO for output in sink.outputs)
    assert not any(
        output.kind in {OutputKind.TTS_START, OutputKind.TTS_STOP}
        for output in sink.outputs
    )
    assert arbiter.snapshot.state is ConversationState.LISTENING
