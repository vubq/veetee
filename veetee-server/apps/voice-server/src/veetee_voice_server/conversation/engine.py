from __future__ import annotations

from typing import Any

from veetee_voice_server.conversation.arbiter import StaleTurnError, TurnArbiter
from veetee_voice_server.conversation.cancellation import (
    OperationContext,
    OperationDeadlineExceededError,
    TurnCancelledError,
    await_operation,
    iterate_operation,
)
from veetee_voice_server.conversation.sentence_chunker import SentenceChunker
from veetee_voice_server.conversation.types import (
    AdmissionDisposition,
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
    ) -> None:
        self._arbiter = arbiter
        self._admission = admission
        self._planner = planner
        self._llm = llm
        self._tts = tts
        self._tools = tools
        self._sink = sink
        self._policy = policy or ConversationPolicy()

    async def handle_transcript(self, transcript: Transcript) -> None:
        context = await self._arbiter.begin_turn(self._policy.total_turn_seconds)
        try:
            decision = await await_operation(
                self._admission.evaluate(
                    transcript, context.child(self._policy.admission_seconds)
                ),
                context.child(self._policy.admission_seconds),
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
                    },
                ),
            )

            if decision.disposition in {
                AdmissionDisposition.NON_ACTIONABLE,
                AdmissionDisposition.NOT_ADDRESSED,
                AdmissionDisposition.UNCLEAR,
            }:
                await self._arbiter.complete_turn(context)
                return
            if decision.disposition is AdmissionDisposition.INTERRUPT:
                receipt = await self._arbiter.abort("semantic_interrupt")
                await self._arbiter.finish_cancellation(receipt)
                return

            plan = await await_operation(
                self._planner.plan(
                    transcript, decision, context.child(self._policy.planner_seconds)
                ),
                context.child(self._policy.planner_seconds),
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
                    },
                ),
            )
            await self._execute_plan(transcript, plan, context)
        except (TurnCancelledError, StaleTurnError):
            return
        except OperationDeadlineExceededError as error:
            await self._emit_if_current_error(context, "provider_deadline", str(error))
        except Exception as error:
            await self._emit_if_current_error(context, "conversation_failed", type(error).__name__)
        finally:
            await self._arbiter.complete_turn(context)

    async def _execute_plan(
        self, transcript: Transcript, plan: ConversationPlan, context: OperationContext
    ) -> None:
        if plan.action in {PlanAction.NOOP, PlanAction.CANCEL_PENDING_TOOL}:
            return
        if plan.action is PlanAction.END_SESSION:
            if plan.response_text:
                await self._speak_text(plan.response_text, plan.locale, context)
            await self._arbiter.close_assistant("semantic_end")
            return
        if plan.action is PlanAction.ASK_CLARIFICATION:
            if plan.response_text:
                await self._speak_text(plan.response_text, plan.locale, context)
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
            await self._stream_response(
                LlmRequest(transcript=transcript, plan=plan, tool_result=tool_result), context
            )

    async def _stream_response(self, request: LlmRequest, context: OperationContext) -> None:
        llm_context = context.child(self._policy.llm_seconds)
        chunker = SentenceChunker(
            min_characters=self._policy.sentence_min_characters,
            abbreviations=self._policy.sentence_abbreviations,
        )
        async for event in iterate_operation(self._llm.stream(request, llm_context), llm_context):
            if not isinstance(event, LlmTextDelta):
                # Planner-owned tool calls are handled before this prose stream in MVP.
                continue
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
                await self._speak_text(sentence, request.plan.locale, context)

        remainder = chunker.flush()
        if remainder:
            await self._speak_text(remainder, request.plan.locale, context)

    async def _speak_text(
        self, text: str, locale: str, context: OperationContext
    ) -> None:
        if self._arbiter.snapshot.state.value == "thinking":
            await self._arbiter.mark_speaking(context)
        tts_context = context.child(self._policy.tts_seconds)
        async for audio in iterate_operation(
            self._tts.synthesize(text, locale, tts_context), tts_context
        ):
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
