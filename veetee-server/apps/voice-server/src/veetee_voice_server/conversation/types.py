from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WakeSource(StrEnum):
    BUTTON = "button"
    WAKE_WORD = "wake_word"


class AdmissionDisposition(StrEnum):
    ACCEPTED = "accepted"
    NON_ACTIONABLE = "non_actionable"
    NOT_ADDRESSED = "not_addressed"
    UNCLEAR = "unclear"
    INTERRUPT = "interrupt"
    END = "end"


class DialogueAct(StrEnum):
    QUESTION = "question"
    COMMAND = "command"
    FOLLOW_UP = "follow_up"
    ANSWER = "answer"
    CONFIRMATION = "confirmation"
    DENIAL = "denial"
    CORRECTION = "correction"
    CLARIFICATION_ANSWER = "clarification_answer"
    SOCIAL = "social"
    INTERRUPT = "interrupt"
    END = "end"


class PlanAction(StrEnum):
    RESPOND = "respond"
    CALL_TOOL_THEN_RESPOND = "call_tool_then_respond"
    ASK_CLARIFICATION = "ask_clarification"
    EXECUTE_PENDING_TOOL = "execute_pending_tool"
    CANCEL_PENDING_TOOL = "cancel_pending_tool"
    END_SESSION = "end_session"
    NOOP = "noop"


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    locale: str
    confidence: float | None = None
    stability: float | None = None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    disposition: AdmissionDisposition
    confidence: float
    reason_code: str
    addressed_to_robot: float | None = None


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConversationPlan:
    action: PlanAction
    dialogue_act: DialogueAct
    locale: str
    intent: str
    response_required: bool
    response_text: str | None = None
    tool_call: ToolCall | None = None


@dataclass(frozen=True, slots=True)
class AudioChunk:
    sequence: int
    sample_rate: int
    encoding: str
    data: bytes
    final: bool = False


class OutputKind(StrEnum):
    ADMISSION = "admission"
    PLAN = "plan"
    TEXT_DELTA = "text_delta"
    AUDIO = "audio"
    CONTROL = "control"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ConversationOutput:
    kind: OutputKind
    turn_id: str | None
    generation: int
    payload: dict[str, Any] = field(default_factory=dict)
    audio: AudioChunk | None = None


@dataclass(frozen=True, slots=True)
class ConversationPolicy:
    first_input_seconds: float = 15.0
    between_turns_seconds: float = 30.0
    closing_grace_seconds: float = 5.0
    max_session_seconds: float = 600.0
    total_turn_seconds: float = 30.0
    admission_seconds: float = 1.0
    planner_seconds: float = 3.0
    llm_seconds: float = 20.0
    tts_seconds: float = 10.0
    mcp_seconds: float = 10.0
    sentence_min_characters: int = 24
    sentence_abbreviations: tuple[str, ...] = ()
