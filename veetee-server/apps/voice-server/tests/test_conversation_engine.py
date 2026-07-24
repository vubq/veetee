from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
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
        self.transcripts: list[Transcript] = []

    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        self.calls += 1
        self.transcripts.append(transcript)
        context.checkpoint()
        return AdmissionDecision(self.disposition, 0.95, "fixture")


class FailingAdmission:
    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        del transcript, context
        raise RuntimeError("provider fixture failure")


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


class SlowFusedGate:
    def __init__(self, plan: ConversationPlan) -> None:
        self.plan_value = plan

    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        await asyncio.sleep(0.02)
        context.checkpoint()
        return AdmissionDecision(AdmissionDisposition.ACCEPTED, 0.95, "fixture")

    async def plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        context: OperationContext,
    ) -> ConversationPlan:
        context.checkpoint()
        return self.plan_value


class UnavailableFusedGate:
    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        del transcript
        context.checkpoint()
        return AdmissionDecision(AdmissionDisposition.ACCEPTED, 0.5, "invalid_model_output")

    async def plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        context: OperationContext,
    ) -> ConversationPlan:
        del transcript, admission
        context.checkpoint()
        return ConversationPlan(
            action=PlanAction.RESPOND,
            dialogue_act=DialogueAct.ANSWER,
            locale="vi-VN",
            intent="",
            response_required=True,
            runtime_error_code="semantic_provider_unavailable",
        )


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


class BlockingTts:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        del locale, context
        self.calls.append(text)
        self.started.set()
        await self.release.wait()
        yield AudioChunk(0, 24000, "pcm_s16le", text.encode(), final=True)


class FailingTts:
    def __init__(self) -> None:
        self.calls = 0

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        del text, locale, context
        self.calls += 1
        if False:
            yield AudioChunk(0, 24000, "pcm_s16le", b"", final=True)
        raise RuntimeError("tts fixture failure")


class TurnScopedTts(FakeTts):
    def __init__(self) -> None:
        super().__init__()
        self.turn_lock = asyncio.Lock()

    @asynccontextmanager
    async def speech_turn(
        self, context: OperationContext
    ) -> AsyncGenerator[None]:
        context.checkpoint()
        async with self.turn_lock:
            yield


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any], context: OperationContext) -> Any:
        self.calls.append((name, arguments))
        return {"ok": True}


def response_plan(response_text: str | None = None) -> ConversationPlan:
    return ConversationPlan(
        action=PlanAction.RESPOND,
        dialogue_act=DialogueAct.QUESTION,
        locale="vi-VN",
        intent="fixture.question",
        response_required=True,
        response_text=response_text,
    )


def create_engine(
    disposition: AdmissionDisposition = AdmissionDisposition.ACCEPTED,
    plan: ConversationPlan | None = None,
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
    planner = FakePlanner(plan or response_plan())
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
        index for index, output in enumerate(sink.outputs) if output.kind is OutputKind.TTS_START
    ) < next(index for index, output in enumerate(sink.outputs) if output.kind is OutputKind.AUDIO)
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_planner_response_text_skips_the_second_llm_call() -> None:
    engine, arbiter, _, planner, llm, tts, sink = create_engine(
        plan=response_plan("Tôi là Veetee.")
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Bạn là ai?", "vi-VN", confidence=0.99))

    assert planner.calls == 1
    assert llm.calls == 0
    assert tts.calls == ["Tôi là Veetee."]
    assert any(output.kind is OutputKind.AUDIO for output in sink.outputs)
    assert any(
        output.kind is OutputKind.TEXT_DELTA and output.payload.get("text") == "Tôi là Veetee."
        for output in sink.outputs
    )


async def test_planner_response_text_uses_sentence_sized_tts_requests() -> None:
    engine, arbiter, _, _, llm, tts, sink = create_engine(
        plan=response_plan("Câu trả lời thứ nhất. Câu trả lời thứ hai.")
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Hãy giải thích", "vi-VN", confidence=0.99))

    assert llm.calls == 0
    assert tts.calls == ["Câu trả lời thứ nhất.", "Câu trả lời thứ hai."]
    kinds = [output.kind for output in sink.outputs]
    assert kinds.count(OutputKind.TTS_START) == 1
    assert kinds.count(OutputKind.TTS_STOP) == 1


async def test_clarification_without_planned_text_uses_the_prose_stream() -> None:
    plan = ConversationPlan(
        action=PlanAction.ASK_CLARIFICATION,
        dialogue_act=DialogueAct.QUESTION,
        locale="vi-VN",
        intent="conversation.clarify",
        response_required=True,
        response_text=None,
    )
    engine, arbiter, _, _, llm, tts, _ = create_engine(plan=plan)
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Ý tôi là cái kia", "vi-VN"))

    assert llm.calls == 1
    assert tts.calls


async def test_contextual_follow_up_keeps_recent_user_and_assistant_turns() -> None:
    engine, arbiter, admission, _, _, _, _ = create_engine(
        plan=response_plan("Tôi vừa nói một câu đùa.")
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Bạn vừa nói gì?", "vi-VN"))
    await engine.handle_transcript(Transcript("Gke vậy sao?", "vi-VN"))

    assert len(admission.transcripts) == 2
    assert admission.transcripts[0].context == ()
    assert [(item.role, item.text) for item in admission.transcripts[1].context] == [
        ("user", "Bạn vừa nói gì?"),
        ("assistant", "Tôi vừa nói một câu đùa."),
    ]


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


async def test_fused_semantic_gate_uses_planner_deadline() -> None:
    arbiter = TurnArbiter("session-fused")
    gate = SlowFusedGate(response_plan("Đã xử lý."))
    llm = FakeLlm()
    tts = FakeTts()
    sink = MemoryConversationSink()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=gate,
        planner=gate,
        llm=llm,
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            admission_seconds=0.001,
            planner_seconds=0.2,
            sentence_min_characters=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    result = await engine.handle_transcript(Transcript("fixture", "vi-VN"))

    assert result is AdmissionDisposition.ACCEPTED
    assert tts.calls == ["Đã xử lý."]
    assert not any(output.kind is OutputKind.ERROR for output in sink.outputs)


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
        output.kind in {OutputKind.TTS_START, OutputKind.TTS_STOP} for output in sink.outputs
    )
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_llm_stream_continues_while_tts_speaks_previous_chunk() -> None:
    arbiter = TurnArbiter("session-pipeline")
    admission = FakeAdmission(AdmissionDisposition.ACCEPTED)
    planner = FakePlanner(response_plan())
    tts = BlockingTts()
    sink = MemoryConversationSink()

    class StreamingLlm:
        def __init__(self) -> None:
            self.second_delta = asyncio.Event()

        async def stream(
            self, request: LlmRequest, context: OperationContext
        ) -> AsyncIterator[LlmTextDelta]:
            del request, context
            yield LlmTextDelta("Đây là câu đầu tiên.")
            self.second_delta.set()
            yield LlmTextDelta(" Đây là câu thứ hai.")

    llm = StreamingLlm()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=admission,
        planner=planner,
        llm=llm,
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            sentence_min_characters=1,
            speech_queue_capacity=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)
    task = asyncio.create_task(engine.handle_transcript(Transcript("fixture", "vi-VN")))

    await asyncio.wait_for(
        asyncio.gather(tts.started.wait(), llm.second_delta.wait()),
        timeout=1.0,
    )
    assert tts.calls == ["Đây là câu đầu tiên."]

    tts.release.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert tts.calls == ["Đây là câu đầu tiên.", "Đây là câu thứ hai."]


async def test_tts_chunk_capability_coalesces_short_sentences_for_natural_pacing() -> None:
    class CoalescingTts(FakeTts):
        preferred_text_chunk_characters = 44

    arbiter = TurnArbiter("session-natural-pacing")
    tts = CoalescingTts()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=FakeLlm(("Đây là câu ngắn đầu tiên.", " Đây là câu ngắn thứ hai.")),
        tts=tts,
        tools=FakeTools(),
        sink=MemoryConversationSink(),
        policy=ConversationPolicy(
            sentence_min_characters=1,
            speech_chunk_target_characters=20,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("fixture", "vi-VN"))

    assert tts.calls == ["Đây là câu ngắn đầu tiên. Đây là câu ngắn thứ hai."]


async def test_long_but_active_stream_is_unbounded_and_retains_only_context_tail() -> None:
    arbiter = TurnArbiter("session-unbounded-response")
    sink = MemoryConversationSink()
    deltas = tuple(f"Đây là phần trả lời số {index}. " for index in range(60))

    class ProgressiveLlm:
        async def stream(
            self, request: LlmRequest, context: OperationContext
        ) -> AsyncIterator[LlmTextDelta]:
            del request, context
            for delta in deltas:
                await asyncio.sleep(0.004)
                yield LlmTextDelta(delta)

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=ProgressiveLlm(),
        tts=FakeTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            llm_first_token_seconds=0.02,
            llm_stream_idle_seconds=0.01,
            context_message_characters=96,
            sentence_min_characters=1,
            speech_queue_capacity=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("Kể một câu chuyện dài.", "vi-VN")),
        timeout=2.0,
    )

    assert len([output for output in sink.outputs if output.kind is OutputKind.TEXT_DELTA]) == 60
    assert len(engine.context[-1].text) <= 96
    assert engine.context[-1].text.endswith("Đây là phần trả lời số 59.")
    assert not any(output.kind is OutputKind.ERROR for output in sink.outputs)


async def test_first_token_deadline_is_shorter_than_stream_idle_deadline() -> None:
    arbiter = TurnArbiter("session-first-token-timeout")
    sink = MemoryConversationSink()

    class SlowFirstTokenLlm:
        async def stream(
            self, request: LlmRequest, context: OperationContext
        ) -> AsyncIterator[LlmTextDelta]:
            del request, context
            await asyncio.sleep(0.04)
            yield LlmTextDelta("Quá muộn.")

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=SlowFirstTokenLlm(),
        tts=FakeTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            llm_first_token_seconds=0.02,
            llm_stream_idle_seconds=0.2,
            sentence_min_characters=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("fixture", "vi-VN")),
        timeout=1.0,
    )

    assert [output.payload for output in sink.outputs if output.kind is OutputKind.ERROR] == [
        {"code": "provider_deadline", "stage": "provider"}
    ]
    assert not any(output.kind is OutputKind.TTS_START for output in sink.outputs)


async def test_empty_tts_chunks_do_not_open_a_false_speaking_lifecycle() -> None:
    arbiter = TurnArbiter("session-empty-tts")
    sink = MemoryConversationSink()

    class EmptyTts:
        async def synthesize(
            self, text: str, locale: str, context: OperationContext
        ) -> AsyncIterator[AudioChunk]:
            del text, locale, context
            yield AudioChunk(0, 24_000, "pcm_s16le", b"", final=True)

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan("Đây là câu trả lời.")),
        llm=FakeLlm(),
        tts=EmptyTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(sentence_min_characters=1),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("fixture", "vi-VN"))

    assert not any(
        output.kind in {OutputKind.TTS_START, OutputKind.AUDIO, OutputKind.TTS_STOP}
        for output in sink.outputs
    )
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_llm_idle_deadline_does_not_limit_long_tts_backpressure() -> None:
    arbiter = TurnArbiter("session-long-response")
    tts = BlockingTts()
    sink = MemoryConversationSink()
    sentences = tuple(f"Đây là đoạn truyện thứ {index}." for index in range(1, 7))
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=FakeLlm(sentences),
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            llm_stream_idle_seconds=0.02,
            sentence_min_characters=1,
            speech_queue_capacity=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)
    task = asyncio.create_task(engine.handle_transcript(Transcript("Kể chuyện.", "vi-VN")))

    await asyncio.wait_for(tts.started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)
    tts.release.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert tts.calls == list(sentences)
    assert not any(output.kind is OutputKind.ERROR for output in sink.outputs)
    stop = next(output for output in sink.outputs if output.kind is OutputKind.TTS_STOP)
    assert stop.payload == {}
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_tts_queue_wait_does_not_consume_synthesis_idle_deadline() -> None:
    arbiter = TurnArbiter("session-tts-queue")
    tts = TurnScopedTts()
    sink = MemoryConversationSink()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan("Đây là câu trả lời.")),
        llm=FakeLlm(),
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            tts_first_audio_seconds=0.02,
            tts_stream_idle_seconds=0.02,
            sentence_min_characters=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)
    await tts.turn_lock.acquire()
    task = asyncio.create_task(
        engine.handle_transcript(Transcript("fixture", "vi-VN"))
    )

    await asyncio.sleep(0.05)
    assert not task.done()
    tts.turn_lock.release()
    await asyncio.wait_for(task, timeout=1.0)

    assert tts.calls == ["Đây là câu trả lời."]
    assert not any(output.kind is OutputKind.ERROR for output in sink.outputs)


async def test_tts_first_audio_deadline_precedes_stream_idle_watchdog() -> None:
    arbiter = TurnArbiter("session-first-audio-timeout")
    sink = MemoryConversationSink()

    class SlowFirstAudioTts:
        async def synthesize(
            self, text: str, locale: str, context: OperationContext
        ) -> AsyncIterator[AudioChunk]:
            del text, locale, context
            await asyncio.sleep(0.04)
            yield AudioChunk(0, 24_000, "pcm_s16le", b"audio")

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan("Đây là câu trả lời.")),
        llm=FakeLlm(),
        tts=SlowFirstAudioTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(
            tts_first_audio_seconds=0.02,
            tts_stream_idle_seconds=0.2,
            sentence_min_characters=1,
        ),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("fixture", "vi-VN")),
        timeout=1.0,
    )

    assert [output.payload for output in sink.outputs if output.kind is OutputKind.ERROR] == [
        {"code": "provider_deadline", "stage": "provider"}
    ]
    assert not any(output.kind is OutputKind.TTS_START for output in sink.outputs)


async def test_tts_idle_deadline_refreshes_while_audio_keeps_progressing() -> None:
    arbiter = TurnArbiter("session-progressive-tts")
    sink = MemoryConversationSink()

    class ProgressiveTts:
        async def synthesize(
            self, text: str, locale: str, context: OperationContext
        ) -> AsyncIterator[AudioChunk]:
            del text, locale, context
            for sequence in range(4):
                await asyncio.sleep(0.012)
                yield AudioChunk(sequence, 24_000, "pcm_s16le", b"audio")

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan("Đây là câu trả lời.")),
        llm=FakeLlm(),
        tts=ProgressiveTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(tts_stream_idle_seconds=0.02, sentence_min_characters=1),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("fixture", "vi-VN")),
        timeout=1.0,
    )

    audio = [output for output in sink.outputs if output.kind is OutputKind.AUDIO]
    assert len(audio) == 4
    assert not any(output.kind is OutputKind.ERROR for output in sink.outputs)


async def test_tts_idle_deadline_cancels_playback_when_audio_stalls() -> None:
    arbiter = TurnArbiter("session-stalled-tts")
    sink = MemoryConversationSink()

    class StalledTts:
        async def synthesize(
            self, text: str, locale: str, context: OperationContext
        ) -> AsyncIterator[AudioChunk]:
            del text, locale, context
            yield AudioChunk(0, 24_000, "pcm_s16le", b"audio")
            await asyncio.Event().wait()

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan("Đây là câu trả lời.")),
        llm=FakeLlm(),
        tts=StalledTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(tts_stream_idle_seconds=0.02, sentence_min_characters=1),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("fixture", "vi-VN")),
        timeout=1.0,
    )

    errors = [output for output in sink.outputs if output.kind is OutputKind.ERROR]
    assert [output.payload for output in errors] == [
        {"code": "provider_deadline", "stage": "provider"}
    ]
    stop = next(output for output in sink.outputs if output.kind is OutputKind.TTS_STOP)
    assert stop.payload == {"cancelled": True}


async def test_llm_idle_deadline_stops_stalled_stream_and_cancels_playback() -> None:
    arbiter = TurnArbiter("session-stalled-response")
    sink = MemoryConversationSink()

    class StalledLlm:
        async def stream(
            self, request: LlmRequest, context: OperationContext
        ) -> AsyncIterator[LlmTextDelta]:
            del request, context
            yield LlmTextDelta("Đây là đoạn đầu tiên.")
            await asyncio.Event().wait()

    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=StalledLlm(),
        tts=FakeTts(),
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(llm_stream_idle_seconds=0.02, sentence_min_characters=1),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await asyncio.wait_for(
        engine.handle_transcript(Transcript("Kể chuyện.", "vi-VN")),
        timeout=1.0,
    )

    errors = [output for output in sink.outputs if output.kind is OutputKind.ERROR]
    assert [output.payload for output in errors] == [
        {"code": "provider_deadline", "stage": "provider"}
    ]
    stop = next(output for output in sink.outputs if output.kind is OutputKind.TTS_STOP)
    assert stop.payload == {"cancelled": True}
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_tts_failure_does_not_leave_llm_producer_blocked_on_full_queue() -> None:
    arbiter = TurnArbiter("session-tts-failure")
    tts = FailingTts()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FakeAdmission(AdmissionDisposition.ACCEPTED),
        planner=FakePlanner(response_plan()),
        llm=FakeLlm(("Một câu đầu tiên.", " Một câu thứ hai.", " Một câu thứ ba.")),
        tts=tts,
        tools=FakeTools(),
        sink=MemoryConversationSink(),
        policy=ConversationPolicy(sentence_min_characters=1, speech_queue_capacity=1),
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    result = await asyncio.wait_for(
        engine.handle_transcript(Transcript("fixture", "vi-VN")),
        timeout=1.0,
    )

    assert tts.calls == 1
    assert result is AdmissionDisposition.ACCEPTED
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_provider_failure_speaks_recovery_and_keeps_session_open() -> None:
    arbiter = TurnArbiter("session-recovery")
    llm = FakeLlm()
    tts = FakeTts()
    sink = MemoryConversationSink()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=FailingAdmission(),
        planner=FakePlanner(response_plan()),
        llm=llm,
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(sentence_min_characters=1),
        error_text="Bạn nói lại giúp tôi nhé.",
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Hôm nay là thứ mấy?", "vi-VN"))

    assert tts.calls == ["Bạn nói lại giúp tôi nhé."]
    errors = [output for output in sink.outputs if output.kind is OutputKind.ERROR]
    assert [output.payload for output in errors] == [
        {"code": "conversation_failed", "stage": "conversation"}
    ]
    assert arbiter.snapshot.state is ConversationState.LISTENING


async def test_semantic_provider_failure_does_not_make_second_llm_call() -> None:
    arbiter = TurnArbiter("session-semantic-unavailable")
    gate = UnavailableFusedGate()
    llm = FakeLlm()
    tts = FakeTts()
    sink = MemoryConversationSink()
    engine = ConversationEngine(
        arbiter=arbiter,
        admission=gate,
        planner=gate,
        llm=llm,
        tts=tts,
        tools=FakeTools(),
        sink=sink,
        policy=ConversationPolicy(sentence_min_characters=1),
        error_text="Bạn nói lại giúp tôi nhé.",
    )
    await arbiter.open_assistant(WakeSource.BUTTON)

    await engine.handle_transcript(Transcript("Hôm nay là thứ mấy?", "vi-VN"))

    assert llm.calls == 0
    assert tts.calls == ["Bạn nói lại giúp tôi nhé."]
    errors = [output.payload for output in sink.outputs if output.kind is OutputKind.ERROR]
    assert errors == [
        {
            "code": "semantic_provider_unavailable",
            "stage": "semantic",
        }
    ]
