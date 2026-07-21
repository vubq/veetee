from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

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
        if len(text) < self.min_characters:
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
        return ConversationPlan(
            action=action,
            dialogue_act=dialogue_act,
            locale=str(value.get("locale", locale)),
            intent=str(value.get("intent", "")),
            response_required=bool(value.get("response_required", True)),
            response_text=value.get("response_text")
            if isinstance(value.get("response_text"), str)
            else None,
            tool_call=tool_call,
        )
