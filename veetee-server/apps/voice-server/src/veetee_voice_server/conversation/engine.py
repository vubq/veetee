from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

import structlog

from veetee_voice_server.conversation.arbiter import (
    ConversationState,
    StaleTurnError,
    TurnArbiter,
)
from veetee_voice_server.conversation.cancellation import (
    OperationContext,
    OperationDeadlineExceededError,
    TurnCancelledError,
    await_operation,
    iterate_operation,
)
from veetee_voice_server.conversation.memory import CompletedMemoryTurn, MemorySnapshot
from veetee_voice_server.conversation.sentence_chunker import (
    SentenceChunker,
    TextChunk,
    TtsTextChunkingPolicy,
)
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AdmissionDisposition,
    ConversationMessage,
    ConversationOutput,
    ConversationPlan,
    ConversationPolicy,
    OutputKind,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.contracts import (
    AdmissionProvider,
    LlmEvent,
    LlmProvider,
    LlmRequest,
    LlmTextDelta,
    PlannerProvider,
    ToolBroker,
    TtsProvider,
)
from veetee_voice_server.transport.sink import ConversationSink

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class _SpeechLifecycle:
    started: bool = False
    batch_count: int = 0


@dataclass(frozen=True, slots=True)
class _AssistantCompletion:
    context_text: str
    durable_text: str


class _SemanticProviderUnavailableError(RuntimeError):
    pass


class ConversationEngine:
    def __init__(
        self,
        *,
        arbiter: TurnArbiter,
        admission: AdmissionProvider,
        planner: PlannerProvider,
        llm: LlmProvider,
        tts: TtsProvider,
        tools: ToolBroker,
        sink: ConversationSink,
        policy: ConversationPolicy | None = None,
        system_prompt: str | None = None,
        error_text: str | None = None,
    ) -> None:
        self._arbiter = arbiter
        self._admission = admission
        self._planner = planner
        self._fused_semantic_gate = id(admission) == id(planner)
        self._llm = llm
        self._tts = tts
        self._tools = tools
        self._sink = sink
        self._policy = policy or ConversationPolicy()
        self._system_prompt = system_prompt
        self._error_text = " ".join((error_text or "").split())[:240]
        self._context: deque[ConversationMessage] = deque(
            maxlen=max(2, min(self._policy.context_message_limit, 32))
        )
        self._cross_session_memory = MemorySnapshot()
        self._completed_turn_sink: Callable[[CompletedMemoryTurn], bool] | None = None
        self._durable_message_characters = max(
            1, self._policy.context_message_characters
        )

    @property
    def context(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._context)

    def configure_cross_session_memory(
        self,
        snapshot: MemorySnapshot,
        completed_turn_sink: Callable[[CompletedMemoryTurn], bool],
        *,
        max_message_characters: int | None = None,
    ) -> None:
        """Attach one preloaded snapshot and a non-blocking completion queue."""

        self._cross_session_memory = snapshot
        self._completed_turn_sink = completed_turn_sink
        if max_message_characters is not None:
            self._durable_message_characters = max(
                1, min(int(max_message_characters), 4_000)
            )

    async def handle_transcript(self, transcript: Transcript) -> AdmissionDisposition | None:
        context = await self._arbiter.begin_turn(self._policy.total_turn_seconds)
        admitted_disposition: AdmissionDisposition | None = None
        try:
            contextual_transcript = replace(
                transcript,
                context=tuple(self._context),
                cross_session_memory=(
                    None if self._cross_session_memory.empty else self._cross_session_memory
                ),
            )
            admission_seconds = (
                self._policy.planner_seconds
                if self._fused_semantic_gate
                else self._policy.admission_seconds
            )
            admission_context = context.child(admission_seconds)
            admission_started_at = monotonic()
            decision = await await_operation(
                self._admission.evaluate(contextual_transcript, admission_context),
                admission_context,
            )
            logger.info(
                "conversation_admission_ready",
                session_id=context.session_id,
                turn_id=context.turn_id,
                duration_ms=round((monotonic() - admission_started_at) * 1_000, 1),
                context_messages=len(contextual_transcript.context),
                input_source=(
                    contextual_transcript.input_evidence.source.value
                    if contextual_transcript.input_evidence is not None
                    else "unknown"
                ),
            )
            await self._emit(
                context,
                ConversationOutput(
                    kind=OutputKind.ADMISSION,
                    turn_id=context.turn_id,
                    generation=context.generation,
                    payload={
                        "disposition": decision.disposition.value,
                        "confidence": decision.confidence,
                        "reason_code": decision.reason_code,
                        "addressed_to_robot": decision.addressed_to_robot,
                        "duration_ms": round(
                            (monotonic() - admission_started_at) * 1_000, 1
                        ),
                    },
                ),
            )

            if decision.disposition in {
                AdmissionDisposition.NON_ACTIONABLE,
                AdmissionDisposition.NOT_ADDRESSED,
                AdmissionDisposition.UNCLEAR,
            }:
                await self._arbiter.complete_turn(context)
                return decision.disposition
            if decision.disposition is AdmissionDisposition.INTERRUPT:
                receipt = await self._arbiter.abort("semantic_interrupt")
                await self._arbiter.finish_cancellation(receipt)
                return decision.disposition

            admitted_disposition = decision.disposition
            planner_started_at = monotonic()
            plan = await await_operation(
                self._planner.plan(
                    contextual_transcript, decision, context.child(self._policy.planner_seconds)
                ),
                context.child(self._policy.planner_seconds),
            )
            logger.info(
                "conversation_plan_ready",
                session_id=context.session_id,
                turn_id=context.turn_id,
                action=plan.action.value,
                dialogue_act=plan.dialogue_act.value,
                response_required=plan.response_required,
                has_tool=plan.tool_call is not None,
                has_response_text=bool(plan.response_text),
                duration_ms=round((monotonic() - planner_started_at) * 1_000, 1),
            )
            if plan.runtime_error_code == "semantic_provider_unavailable":
                raise _SemanticProviderUnavailableError(
                    "semantic provider unavailable"
                )
            await self._emit(
                context,
                ConversationOutput(
                    kind=OutputKind.PLAN,
                    turn_id=context.turn_id,
                    generation=context.generation,
                    payload={
                        "action": plan.action.value,
                        "dialogue_act": plan.dialogue_act.value,
                        "intent": plan.intent,
                        "locale": plan.locale,
                        "tool_name": plan.tool_call.name if plan.tool_call is not None else None,
                        "duration_ms": round(
                            (monotonic() - planner_started_at) * 1_000, 1
                        ),
                    },
                ),
            )
            assistant = await self._execute_plan(
                contextual_transcript, decision, plan, context
            )
            if assistant is not None and assistant.durable_text:
                context.checkpoint()
                self._remember("user", contextual_transcript.text)
                self._remember("assistant", assistant.context_text)
                self._record_completed_turn(
                    context,
                    contextual_transcript.text,
                    assistant.durable_text,
                    plan,
                )
            if plan.action is PlanAction.END_SESSION:
                await self._arbiter.close_assistant("semantic_end")
            return decision.disposition
        except (TurnCancelledError, StaleTurnError):
            return None
        except OperationDeadlineExceededError:
            logger.warning(
                "conversation_provider_deadline",
                session_id=context.session_id,
                turn_id=context.turn_id,
                error_code="provider_deadline",
            )
            await self._speak_recovery_if_possible(context, transcript)
            await self._emit_if_current_error(context, "provider_deadline", "provider")
            return admitted_disposition
        except _SemanticProviderUnavailableError:
            logger.warning(
                "conversation_semantic_provider_unavailable",
                session_id=context.session_id,
                turn_id=context.turn_id,
            )
            await self._speak_recovery_if_possible(context, transcript)
            await self._emit_if_current_error(
                context,
                "semantic_provider_unavailable",
                "semantic",
            )
            return admitted_disposition
        except Exception as error:
            logger.warning(
                "conversation_turn_failed",
                session_id=context.session_id,
                turn_id=context.turn_id,
                error=type(error).__name__,
                error_code=getattr(error, "code", type(error).__name__),
                status_code=getattr(error, "status_code", None),
                retryable=getattr(error, "retryable", None),
            )
            await self._speak_recovery_if_possible(context, transcript)
            await self._emit_if_current_error(context, "conversation_failed", "conversation")
            return admitted_disposition
        finally:
            await self._arbiter.complete_turn(context)

    async def _execute_plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        plan: ConversationPlan,
        context: OperationContext,
    ) -> _AssistantCompletion | None:
        if plan.action in {PlanAction.NOOP, PlanAction.CANCEL_PENDING_TOOL}:
            return None
        if plan.action is PlanAction.END_SESSION:
            if plan.response_text:
                await self._speak_planned_text(plan.response_text, plan.locale, context)
            return (
                _AssistantCompletion(plan.response_text, plan.response_text)
                if plan.response_text
                else None
            )
        if plan.action is PlanAction.ASK_CLARIFICATION and plan.response_text:
            await self._speak_planned_text(plan.response_text, plan.locale, context)
            return _AssistantCompletion(plan.response_text, plan.response_text)
        if plan.action is PlanAction.RESPOND and plan.response_text:
            await self._speak_planned_text(plan.response_text, plan.locale, context)
            return _AssistantCompletion(plan.response_text, plan.response_text)

        tool_result: Any | None = None
        if plan.action in {
            PlanAction.CALL_TOOL_THEN_RESPOND,
            PlanAction.EXECUTE_PENDING_TOOL,
        }:
            if plan.tool_call is None:
                raise ValueError("Tool plan is missing tool_call")
            tool_context = context.child(self._policy.mcp_seconds)
            tool_result = await await_operation(
                self._tools.call(
                    plan.tool_call.name,
                    plan.tool_call.arguments,
                    tool_context,
                ),
                tool_context,
            )

        if plan.response_required:
            return await self._stream_response(
                LlmRequest(
                    transcript=transcript,
                    plan=plan,
                    admission=admission,
                    tool_result=tool_result,
                    system_prompt=self._system_prompt,
                ),
                context,
            )
        return None

    async def _stream_response(
        self, request: LlmRequest, context: OperationContext
    ) -> _AssistantCompletion:
        speech = _SpeechLifecycle()
        speech_queue: asyncio.Queue[TextChunk | None] = asyncio.Queue(
            maxsize=self._policy.speech_queue_capacity
        )
        speech_task = asyncio.create_task(
            self._drain_speech_queue(speech_queue, request.plan.locale, context, speech),
            name=f"speech-stream:{context.session_id}:{context.turn_id}",
        )
        response_tail = ""
        response_prefix = ""
        response_characters = 0
        stream_started_at = monotonic()
        first_delta_at: float | None = None
        chunker = self._new_sentence_chunker()
        completed = False
        try:
            llm_context = (
                context.child(self._policy.llm_total_seconds)
                if self._policy.llm_total_seconds > 0
                else context
            )
            async for event in iterate_operation(
                self._llm.stream(request, llm_context),
                llm_context,
                first_item_timeout_seconds=self._policy.llm_first_token_seconds,
                idle_timeout_seconds=self._policy.llm_stream_idle_seconds,
                is_progress=self._is_spoken_llm_progress,
            ):
                if not isinstance(event, LlmTextDelta):
                    # Planner-owned tool calls are handled before this prose stream in MVP.
                    continue
                if first_delta_at is None:
                    first_delta_at = monotonic()
                    logger.info(
                        "conversation_llm_first_token",
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        duration_ms=round((first_delta_at - stream_started_at) * 1_000, 1),
                    )
                if len(event.text) > self._policy.llm_delta_max_characters:
                    raise ValueError("LLM delta exceeds the bounded streaming contract")
                response_characters += len(event.text)
                if len(response_prefix) < self._durable_message_characters:
                    response_prefix = (response_prefix + event.text)[
                        : self._durable_message_characters
                    ]
                response_tail = (response_tail + event.text)[
                    -max(
                        1,
                        self._policy.context_message_characters,
                        self._durable_message_characters,
                    ) :
                ]
                await self._emit(
                    context,
                    ConversationOutput(
                        kind=OutputKind.TEXT_DELTA,
                        turn_id=context.turn_id,
                        generation=context.generation,
                        payload={"text": event.text},
                    ),
                )
                for sentence in chunker.push_chunks(event.text):
                    logger.info(
                        "conversation_tts_text_chunk_ready",
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        reason=sentence.reason,
                        text_characters=len(sentence.text),
                    )
                    await self._enqueue_speech(
                        speech_queue, sentence, speech_task, context
                    )

            logger.info(
                "conversation_llm_stream_complete",
                session_id=context.session_id,
                turn_id=context.turn_id,
                duration_ms=round((monotonic() - stream_started_at) * 1_000, 1),
                response_characters=response_characters,
            )
            for remainder in chunker.flush_chunks():
                logger.info(
                    "conversation_tts_text_chunk_ready",
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    reason=remainder.reason,
                    text_characters=len(remainder.text),
                )
                await self._enqueue_speech(
                    speech_queue, remainder, speech_task, context
                )
            await self._enqueue_speech(speech_queue, None, speech_task, context)
            await await_operation(speech_task, context)
            completed = True
        finally:
            if not speech_task.done():
                speech_task.cancel()
            await asyncio.gather(speech_task, return_exceptions=True)
            await self._finish_speech(context, speech, cancelled=not completed)
        context_text = response_tail[
            -max(1, self._policy.context_message_characters) :
        ].strip()
        if response_characters <= self._durable_message_characters:
            durable_text = response_prefix.strip()
        else:
            separator = " … "
            head_characters = max(
                1, (self._durable_message_characters - len(separator)) // 2
            )
            tail_characters = max(
                1,
                self._durable_message_characters
                - head_characters
                - len(separator),
            )
            durable_text = (
                response_prefix[:head_characters].rstrip()
                + separator
                + response_tail[-tail_characters:].lstrip()
            )[: self._durable_message_characters].strip()
        return _AssistantCompletion(context_text, durable_text)

    async def _drain_speech_queue(
        self,
        queue: asyncio.Queue[TextChunk | None],
        locale: str,
        context: OperationContext,
        speech: _SpeechLifecycle,
    ) -> None:
        chunk = await await_operation(queue.get(), context)
        if chunk is None:
            return
        async with self._speech_turn_scope(context):
            while True:
                await self._speak_text(
                    chunk.text,
                    locale,
                    context,
                    speech,
                    reason=chunk.reason,
                )
                chunk = await await_operation(queue.get(), context)
                if chunk is None:
                    return

    @asynccontextmanager
    async def _speech_turn_scope(
        self, context: OperationContext
    ) -> AsyncGenerator[None]:
        scope_factory = getattr(self._tts, "speech_turn", None)
        if scope_factory is None:
            yield
            return
        async with scope_factory(context):
            yield

    async def _enqueue_speech(
        self,
        queue: asyncio.Queue[TextChunk | None],
        chunk: TextChunk | None,
        speech_task: asyncio.Task[None],
        context: OperationContext,
    ) -> None:
        context.checkpoint()
        if speech_task.done():
            await speech_task
        put_task = asyncio.create_task(queue.put(chunk))
        try:
            # A failed TTS consumer must wake a producer that is blocked on a
            # full queue instead of waiting for the turn deadline forever.
            done, _ = await await_operation(
                asyncio.wait(
                    {put_task, speech_task},
                    return_when=asyncio.FIRST_COMPLETED,
                ),
                context,
            )
            if speech_task in done:
                await speech_task
            await put_task
        finally:
            if not put_task.done():
                put_task.cancel()
            await asyncio.gather(put_task, return_exceptions=True)
        context.checkpoint()

    async def _speak_once(self, text: str, locale: str, context: OperationContext) -> None:
        speech = _SpeechLifecycle()
        speech_queue: asyncio.Queue[TextChunk | None] = asyncio.Queue(
            maxsize=self._policy.speech_queue_capacity
        )
        speech_task = asyncio.create_task(
            self._drain_speech_queue(speech_queue, locale, context, speech),
            name=f"speech-once:{context.session_id}:{context.turn_id}",
        )
        chunker = self._new_sentence_chunker()
        completed = False
        try:
            for sentence in chunker.push_chunks(text):
                await self._enqueue_speech(speech_queue, sentence, speech_task, context)
            for remainder in chunker.flush_chunks():
                logger.info(
                    "conversation_tts_text_chunk_ready",
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    reason=remainder.reason,
                    text_characters=len(remainder.text),
                )
                await self._enqueue_speech(
                    speech_queue, remainder, speech_task, context
                )
            await self._enqueue_speech(speech_queue, None, speech_task, context)
            await await_operation(speech_task, context)
            completed = True
        finally:
            if not speech_task.done():
                speech_task.cancel()
            await asyncio.gather(speech_task, return_exceptions=True)
            await self._finish_speech(context, speech, cancelled=not completed)

    async def _speak_planned_text(self, text: str, locale: str, context: OperationContext) -> None:
        await self._emit(
            context,
            ConversationOutput(
                kind=OutputKind.TEXT_DELTA,
                turn_id=context.turn_id,
                generation=context.generation,
                payload={"text": text},
            ),
        )
        await self._speak_once(text, locale, context)

    async def _speak_text(
        self,
        text: str,
        locale: str,
        context: OperationContext,
        speech: _SpeechLifecycle,
        *,
        reason: str,
    ) -> None:
        started_at = monotonic()
        first_audio_logged = False
        provider = type(self._tts).__name__
        logger.info(
            "conversation_tts_request",
            session_id=context.session_id,
            turn_id=context.turn_id,
            provider=provider,
            text_characters=len(text),
            reason=reason,
        )
        try:
            tts_context = (
                context.child(self._policy.tts_total_seconds)
                if self._policy.tts_total_seconds > 0
                else context
            )
            first_progress_seconds = (
                self._policy.tts_first_audio_seconds
                if not speech.started
                else self._policy.tts_stream_idle_seconds
            )
            speech.batch_count += 1
            async for audio in iterate_operation(
                self._tts.synthesize(text, locale, tts_context),
                tts_context,
                first_item_timeout_seconds=first_progress_seconds,
                idle_timeout_seconds=self._policy.tts_stream_idle_seconds,
                is_progress=lambda chunk: bool(chunk.data),
            ):
                if not audio.data:
                    continue
                if not first_audio_logged:
                    first_audio_logged = True
                    duration_ms = round((monotonic() - started_at) * 1_000, 1)
                    logger.info(
                        "conversation_tts_batch_first_audio",
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        provider=provider,
                        batch_number=speech.batch_count,
                        text_characters=len(text),
                        reason=reason,
                        duration_ms=duration_ms,
                    )
                    if not speech.started:
                        logger.info(
                            "conversation_tts_first_audio",
                            session_id=context.session_id,
                            turn_id=context.turn_id,
                            provider=provider,
                            text_characters=len(text),
                            duration_ms=duration_ms,
                        )
                if not speech.started:
                    await self._arbiter.mark_speaking(context)
                    await self._emit(
                        context,
                        ConversationOutput(
                            kind=OutputKind.TTS_START,
                            turn_id=context.turn_id,
                            generation=context.generation,
                        ),
                    )
                    speech.started = True
                await self._emit(
                    context,
                    ConversationOutput(
                        kind=OutputKind.AUDIO,
                        turn_id=context.turn_id,
                        generation=context.generation,
                        payload={"text": text, "locale": locale},
                        audio=audio,
                    ),
                )
        except (TurnCancelledError, StaleTurnError):
            raise
        except Exception as error:
            logger.warning(
                "conversation_tts_failed",
                session_id=context.session_id,
                turn_id=context.turn_id,
                provider=provider,
                text_characters=len(text),
                duration_ms=round((monotonic() - started_at) * 1_000, 1),
                error=type(error).__name__,
            )
            raise

    @staticmethod
    def _is_spoken_llm_progress(event: LlmEvent) -> bool:
        return isinstance(event, LlmTextDelta) and bool(event.text)

    def _new_sentence_chunker(self) -> SentenceChunker:
        chunking_policy = getattr(
            self._tts,
            "text_chunking_policy",
            TtsTextChunkingPolicy(
                emergency_max_characters=self._policy.speech_chunk_max_characters
            ),
        )
        provider_target = getattr(
            self._tts,
            "preferred_text_chunk_characters",
            self._policy.speech_chunk_target_characters,
        )
        target_characters = max(
            self._policy.speech_chunk_target_characters,
            int(provider_target),
        )
        provider_maximum = getattr(
            self._tts,
            "maximum_text_chunk_characters",
            self._policy.speech_chunk_max_characters,
        )
        max_characters = max(
            target_characters,
            min(self._policy.speech_chunk_max_characters, int(provider_maximum)),
        )
        has_initial_chunk_capability = hasattr(
            self._tts, "initial_text_chunk_characters"
        )
        provider_initial_target = getattr(
            self._tts,
            "initial_text_chunk_characters",
            target_characters,
        )
        initial_target_characters = max(
            self._policy.sentence_min_characters,
            min(target_characters, int(provider_initial_target)),
        )
        provider_initial_maximum = getattr(
            self._tts,
            "initial_maximum_text_chunk_characters",
            max_characters,
        )
        initial_max_characters = max(
            initial_target_characters,
            min(max_characters, int(provider_initial_maximum)),
        )
        return SentenceChunker(
            min_characters=self._policy.sentence_min_characters,
            abbreviations=self._policy.sentence_abbreviations,
            target_characters=target_characters,
            max_characters=max_characters,
            punctuation_min_characters=(
                target_characters
                if hasattr(self._tts, "preferred_text_chunk_characters")
                else self._policy.sentence_min_characters
            ),
            initial_target_characters=initial_target_characters,
            initial_max_characters=initial_max_characters,
            initial_punctuation_min_characters=(
                self._policy.sentence_min_characters
                if has_initial_chunk_capability
                else (
                    target_characters
                    if hasattr(self._tts, "preferred_text_chunk_characters")
                    else self._policy.sentence_min_characters
                )
            ),
            mode=chunking_policy.mode,
            emergency_max_characters=chunking_policy.emergency_max_characters,
            sentence_batch_max_characters=(
                chunking_policy.sentence_batch_max_characters
            ),
        )

    async def _finish_speech(
        self,
        context: OperationContext,
        speech: _SpeechLifecycle,
        *,
        cancelled: bool,
    ) -> None:
        if not speech.started:
            return
        await self._emit_cleanup_if_current(
            context,
            ConversationOutput(
                kind=OutputKind.TTS_STOP,
                turn_id=context.turn_id,
                generation=context.generation,
                payload={"cancelled": True} if cancelled else {},
            ),
        )

    async def _emit(self, context: OperationContext, output: ConversationOutput) -> None:
        self._arbiter.require_current(context)
        await self._sink.emit(output)

    async def _emit_cleanup_if_current(
        self,
        context: OperationContext,
        output: ConversationOutput,
    ) -> None:
        # A parent turn deadline may expire after audio has started. The same
        # generation still needs a terminal cleanup event even though no new
        # provider output is allowed past that deadline.
        if not self._arbiter.is_current(context):
            return
        await self._sink.emit(output)

    async def _emit_if_current_error(
        self, context: OperationContext, code: str, stage: str
    ) -> None:
        if not self._arbiter.is_current(context):
            return
        await self._sink.emit(
            ConversationOutput(
                kind=OutputKind.ERROR,
                turn_id=context.turn_id,
                generation=context.generation,
                payload={"code": code, "stage": stage},
            )
        )

    async def _speak_recovery_if_possible(
        self, context: OperationContext, transcript: Transcript
    ) -> None:
        if (
            not self._error_text
            or self._arbiter.snapshot.state is not ConversationState.THINKING
            or not self._arbiter.is_current(context)
        ):
            return
        try:
            await self._speak_planned_text(self._error_text, transcript.locale, context)
        except (TurnCancelledError, StaleTurnError, OperationDeadlineExceededError):
            return
        except Exception:
            logger.warning(
                "conversation_recovery_response_failed",
                session_id=context.session_id,
                turn_id=context.turn_id,
            )

    def _remember(self, role: Literal["user", "assistant"], text: str) -> None:
        bounded = " ".join(text.split())[: max(1, self._policy.context_message_characters)]
        if not bounded:
            return
        self._context.append(ConversationMessage(role, bounded))

    def _record_completed_turn(
        self,
        context: OperationContext,
        user_text: str,
        assistant_text: str,
        plan: ConversationPlan,
    ) -> None:
        sink = self._completed_turn_sink
        if sink is None:
            return
        try:
            queued = sink(
                CompletedMemoryTurn(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    fact_candidates=plan.memory_facts,
                    occurred_at=datetime.now(UTC).isoformat(),
                )
            )
        except Exception as error:
            logger.warning(
                "conversation_memory_enqueue_degraded",
                session_id=context.session_id,
                turn_id=context.turn_id,
                error_type=type(error).__name__,
            )
            return
        if not queued:
            logger.warning(
                "conversation_memory_enqueue_skipped",
                session_id=context.session_id,
                turn_id=context.turn_id,
            )
