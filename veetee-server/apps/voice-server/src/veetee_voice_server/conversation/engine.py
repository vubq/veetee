from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
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
from veetee_voice_server.conversation.sentence_chunker import SentenceChunker
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

    async def handle_transcript(self, transcript: Transcript) -> AdmissionDisposition | None:
        context = await self._arbiter.begin_turn(self._policy.total_turn_seconds)
        try:
            contextual_transcript = replace(transcript, context=tuple(self._context))
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

            self._remember("user", contextual_transcript.text)
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
                    },
                ),
            )
            await self._execute_plan(contextual_transcript, decision, plan, context)
            return decision.disposition
        except (TurnCancelledError, StaleTurnError):
            return None
        except OperationDeadlineExceededError as error:
            logger.warning(
                "conversation_provider_deadline",
                session_id=context.session_id,
                turn_id=context.turn_id,
                error_code="provider_deadline",
            )
            await self._speak_recovery_if_possible(context, transcript)
            await self._emit_if_current_error(context, "provider_deadline", str(error))
            return None
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
            await self._emit_if_current_error(context, "conversation_failed", type(error).__name__)
            return None
        finally:
            await self._arbiter.complete_turn(context)

    async def _execute_plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        plan: ConversationPlan,
        context: OperationContext,
    ) -> None:
        if plan.action in {PlanAction.NOOP, PlanAction.CANCEL_PENDING_TOOL}:
            return
        if plan.action is PlanAction.END_SESSION:
            if plan.response_text:
                await self._speak_planned_text(plan.response_text, plan.locale, context)
                self._remember("assistant", plan.response_text)
            await self._arbiter.close_assistant("semantic_end")
            return
        if plan.action is PlanAction.ASK_CLARIFICATION:
            if plan.response_text:
                await self._speak_planned_text(plan.response_text, plan.locale, context)
                self._remember("assistant", plan.response_text)
            return
        if plan.action is PlanAction.RESPOND and plan.response_text:
            await self._speak_planned_text(plan.response_text, plan.locale, context)
            self._remember("assistant", plan.response_text)
            return

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
            response_text = await self._stream_response(
                LlmRequest(
                    transcript=transcript,
                    plan=plan,
                    admission=admission,
                    tool_result=tool_result,
                    system_prompt=self._system_prompt,
                ),
                context,
            )
            if response_text:
                self._remember("assistant", response_text)

    async def _stream_response(self, request: LlmRequest, context: OperationContext) -> str:
        llm_context = context.child(self._policy.llm_seconds)
        speech = _SpeechLifecycle()
        response_parts: list[str] = []
        chunker = SentenceChunker(
            min_characters=self._policy.sentence_min_characters,
            abbreviations=self._policy.sentence_abbreviations,
        )
        try:
            async for event in iterate_operation(
                self._llm.stream(request, llm_context), llm_context
            ):
                if not isinstance(event, LlmTextDelta):
                    # Planner-owned tool calls are handled before this prose stream in MVP.
                    continue
                response_parts.append(event.text)
                await self._emit(
                    context,
                    ConversationOutput(
                        kind=OutputKind.TEXT_DELTA,
                        turn_id=context.turn_id,
                        generation=context.generation,
                        payload={"text": event.text},
                    ),
                )
                for sentence in chunker.push(event.text):
                    await self._speak_text(sentence, request.plan.locale, context, speech)

            remainder = chunker.flush()
            if remainder:
                await self._speak_text(remainder, request.plan.locale, context, speech)
        finally:
            await self._finish_speech(context, speech)
        return "".join(response_parts).strip()

    async def _speak_once(self, text: str, locale: str, context: OperationContext) -> None:
        speech = _SpeechLifecycle()
        chunker = SentenceChunker(
            min_characters=self._policy.sentence_min_characters,
            abbreviations=self._policy.sentence_abbreviations,
        )
        try:
            for sentence in chunker.push(text):
                await self._speak_text(sentence, locale, context, speech)
            remainder = chunker.flush()
            if remainder:
                await self._speak_text(remainder, locale, context, speech)
        finally:
            await self._finish_speech(context, speech)

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
    ) -> None:
        tts_context = context.child(self._policy.tts_seconds)
        async for audio in iterate_operation(
            self._tts.synthesize(text, locale, tts_context), tts_context
        ):
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

    async def _finish_speech(self, context: OperationContext, speech: _SpeechLifecycle) -> None:
        if not speech.started or not self._arbiter.is_current(context):
            return
        await self._emit(
            context,
            ConversationOutput(
                kind=OutputKind.TTS_STOP,
                turn_id=context.turn_id,
                generation=context.generation,
            ),
        )

    async def _emit(self, context: OperationContext, output: ConversationOutput) -> None:
        self._arbiter.require_current(context)
        await self._sink.emit(output)

    async def _emit_if_current_error(
        self, context: OperationContext, code: str, detail: str
    ) -> None:
        if not self._arbiter.is_current(context):
            return
        await self._sink.emit(
            ConversationOutput(
                kind=OutputKind.ERROR,
                turn_id=context.turn_id,
                generation=context.generation,
                payload={"code": code, "detail": detail},
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
