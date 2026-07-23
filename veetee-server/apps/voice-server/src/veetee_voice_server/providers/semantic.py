from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AdmissionDisposition,
    ConversationPlan,
    DialogueAct,
    PlanAction,
    ToolCall,
    Transcript,
)


@dataclass(slots=True)
class LocalAdmissionProvider:
    """Cheap pre-LLM gate for empty/obviously unusable ASR results.

    This is intentionally signal-based rather than an intent or phrase list. The
    planner remains responsible for semantic interpretation after this gate.
    """

    min_characters: int = 2
    min_confidence: float = 0.0

    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        context.checkpoint()
        text = " ".join(transcript.text.split())
        if not text:
            return AdmissionDecision(AdmissionDisposition.NON_ACTIONABLE, 1.0, "empty_transcript")
        if transcript.confidence is not None and transcript.confidence < self.min_confidence:
            return AdmissionDecision(
                AdmissionDisposition.UNCLEAR, 1.0 - transcript.confidence, "low_asr_confidence"
            )
        # A short reply can be meaningful after the assistant has just spoken.
        if len(text) < self.min_characters and not transcript.context:
            return AdmissionDecision(
                AdmissionDisposition.NON_ACTIONABLE, 0.9, "transcript_too_short"
            )
        # A transcript made only of repeated punctuation is not a user request.
        if not re.search(r"[\w\u00C0-\u024F]", text, flags=re.UNICODE):
            return AdmissionDecision(
                AdmissionDisposition.NON_ACTIONABLE, 0.95, "no_linguistic_signal"
            )
        return AdmissionDecision(AdmissionDisposition.ACCEPTED, 0.75, "local_signal_pass")


class JsonPlannerProvider:
    """Planner boundary for a structured model adapter.

    The callable is injected so the transport does not know about a vendor SDK.
    """

    def __init__(self, complete_json: Any, *, locale: str = "vi-VN") -> None:
        self._complete_json = complete_json
        self._locale = locale

    async def plan(
        self, transcript: Transcript, admission: AdmissionDecision, context: OperationContext
    ) -> ConversationPlan:
        context.checkpoint()
        result = await self._complete_json(
            {
                "role": "planner",
                "locale": transcript.locale or self._locale,
                "transcript": transcript.text,
                "admission": admission.disposition.value,
                "conversation_context": [
                    {"role": item.role, "text": item.text} for item in transcript.context
                ],
            },
            context,
        )
        return self._parse(result, transcript.locale or self._locale)

    @staticmethod
    def _parse(value: dict[str, Any], locale: str) -> ConversationPlan:
        try:
            action = PlanAction(str(value.get("action", PlanAction.RESPOND.value)))
        except ValueError:
            action = PlanAction.RESPOND
        try:
            dialogue_act = DialogueAct(str(value.get("dialogue_act", DialogueAct.ANSWER.value)))
        except ValueError:
            dialogue_act = DialogueAct.ANSWER
        tool = value.get("tool_call")
        tool_call = None
        if (
            isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
            and isinstance(tool.get("arguments"), dict)
        ):
            tool_call = ToolCall(tool["name"], tool["arguments"])
        response_required = bool(value.get("response_required", True))
        if action in {
            PlanAction.RESPOND,
            PlanAction.CALL_TOOL_THEN_RESPOND,
            PlanAction.EXECUTE_PENDING_TOOL,
        }:
            response_required = True
        elif action in {PlanAction.NOOP, PlanAction.CANCEL_PENDING_TOOL}:
            response_required = False
        return ConversationPlan(
            action=action,
            dialogue_act=dialogue_act,
            locale=str(value.get("locale", locale)),
            intent=str(value.get("intent", "")),
            response_required=response_required,
            response_text=value.get("response_text")
            if isinstance(value.get("response_text"), str)
            else None,
            tool_call=tool_call,
        )


class StructuredConversationGate:
    """Run signal admission and semantic planning in one structured model call."""

    _reason_codes: ClassVar[frozenset[str]] = frozenset(
        {
            "speech_relevant",
            "non_speech",
            "low_quality",
            "not_addressed",
            "self_echo",
            "duplicate",
            "low_confidence",
            "semantic_interrupt",
            "conversation_end",
            "unclear",
            "invalid_model_output",
        }
    )

    def __init__(
        self,
        complete_json: Any,
        *,
        locale: str = "vi-VN",
        signal_gate: LocalAdmissionProvider | None = None,
    ) -> None:
        self._complete_json = complete_json
        self._locale = locale
        self._signal_gate = signal_gate or LocalAdmissionProvider()
        self._cached: tuple[str, int, str, AdmissionDecision, ConversationPlan] | None = None

    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision:
        # Never allow a cancelled or malformed turn to inherit an older plan.
        self._cached = None
        signal_decision = await self._signal_gate.evaluate(transcript, context)
        if signal_decision.disposition is not AdmissionDisposition.ACCEPTED:
            return signal_decision

        value = await self._complete_json(
            {
                "role": "conversation_gate",
                "locale": transcript.locale or self._locale,
                "transcript": transcript.text,
                "asr_confidence": transcript.confidence,
                "asr_stability": transcript.stability,
                "conversation_context": [
                    {"role": item.role, "text": item.text} for item in transcript.context
                ],
            },
            context,
        )
        context.checkpoint()
        decision, plan = self._parse_gate(value, transcript.locale or self._locale)
        if decision.disposition in {AdmissionDisposition.ACCEPTED, AdmissionDisposition.END}:
            self._cached = (
                context.turn_id,
                context.generation,
                transcript.text,
                decision,
                plan,
            )
        return decision

    async def plan(
        self,
        transcript: Transcript,
        admission: AdmissionDecision,
        context: OperationContext,
    ) -> ConversationPlan:
        context.checkpoint()
        cached = self._cached
        self._cached = None
        if (
            cached is None
            or cached[0] != context.turn_id
            or cached[1] != context.generation
            or cached[2] != transcript.text
            or cached[3].disposition is not admission.disposition
        ):
            raise ValueError("Semantic gate plan is unavailable for this turn")
        return cached[4]

    @classmethod
    def _parse_gate(
        cls, value: dict[str, Any], locale: str
    ) -> tuple[AdmissionDecision, ConversationPlan]:
        admission_value = value.get("admission")
        if not isinstance(admission_value, dict):
            admission_value = {}
        try:
            disposition = AdmissionDisposition(
                str(admission_value.get("decision", AdmissionDisposition.UNCLEAR.value))
            )
        except ValueError:
            disposition = AdmissionDisposition.UNCLEAR
        confidence_value = admission_value.get("confidence", 0.0)
        confidence = (
            min(max(float(confidence_value), 0.0), 1.0)
            if isinstance(confidence_value, int | float) and not isinstance(confidence_value, bool)
            else 0.0
        )
        addressed_value = admission_value.get("addressed_to_robot")
        addressed_to_robot = (
            min(max(float(addressed_value), 0.0), 1.0)
            if isinstance(addressed_value, int | float) and not isinstance(addressed_value, bool)
            else None
        )
        reason_code = str(admission_value.get("reason_code", "invalid_model_output"))
        if reason_code not in cls._reason_codes:
            reason_code = "invalid_model_output"

        plan_value = value.get("plan")
        if not isinstance(plan_value, dict):
            plan_value = {}
        flattened_plan = {**plan_value, "dialogue_act": value.get("dialogue_act")}
        plan = JsonPlannerProvider._parse(flattened_plan, locale)

        if (
            disposition is AdmissionDisposition.INTERRUPT
            or plan.dialogue_act is DialogueAct.INTERRUPT
        ):
            disposition = AdmissionDisposition.INTERRUPT
            reason_code = "semantic_interrupt"
        elif (
            disposition is AdmissionDisposition.END
            or plan.dialogue_act is DialogueAct.END
            or plan.action is PlanAction.END_SESSION
        ):
            disposition = AdmissionDisposition.END
            reason_code = "conversation_end"
            plan = replace(
                plan,
                action=PlanAction.END_SESSION,
                dialogue_act=DialogueAct.END,
                tool_call=None,
            )
        elif disposition is AdmissionDisposition.ACCEPTED and plan.action is PlanAction.NOOP:
            disposition = AdmissionDisposition.NON_ACTIONABLE
            reason_code = "unclear"

        if disposition not in {AdmissionDisposition.ACCEPTED, AdmissionDisposition.END}:
            plan = replace(
                plan,
                action=PlanAction.NOOP,
                response_required=False,
                response_text=None,
                tool_call=None,
            )
        return (
            AdmissionDecision(
                disposition,
                confidence,
                reason_code,
                addressed_to_robot,
            ),
            plan,
        )
