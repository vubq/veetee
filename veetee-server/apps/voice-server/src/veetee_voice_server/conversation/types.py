from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class WakeSource(StrEnum):
    BUTTON = "button"
    WAKE_WORD = "wake_word"


class InputSource(StrEnum):
    DEVICE_MIC = "device_mic"
    AUDIO_REPLAY = "audio_replay"
    LIVE_MIC = "live_mic"
    TYPED_TEXT = "typed_text"


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
class ConversationMessage:
    """Bounded in-memory context passed to semantic and response providers."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True, slots=True)
class InputEvidence:
    """Bounded per-utterance evidence; unavailable measurements stay null."""

    source: InputSource
    wake_source: WakeSource | None = None
    utterance_duration_ms: int | None = None
    vad_mean_probability: float | None = None
    vad_peak_probability: float | None = None
    vad_speech_ratio: float | None = None
    signal_rms_dbfs: float | None = None
    signal_peak_dbfs: float | None = None
    noise_rms_dbfs: float | None = None
    estimated_snr_db: float | None = None
    clipping_ratio: float | None = None
    server_buffer_truncated: bool = False
    packet_loss_ratio: float | None = None
    audio_overrun: bool | None = None
    aec_enabled: bool = False
    self_echo_probability: float | None = None
    target_speaker_probability: float | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    locale: str
    confidence: float | None = None
    stability: float | None = None
    context: tuple[ConversationMessage, ...] = ()
    input_evidence: InputEvidence | None = None


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
    TTS_START = "tts_start"
    AUDIO = "audio"
    TTS_STOP = "tts_stop"
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
    first_input_seconds: float = 180.0
    between_turns_seconds: float = 180.0
    closing_grace_seconds: float = 5.0
    max_session_seconds: float = 0.0
    total_turn_seconds: float = 0.0
    admission_seconds: float = 1.0
    planner_seconds: float = 3.0
    llm_seconds: float = 20.0
    tts_seconds: float = 10.0
    mcp_seconds: float = 10.0
    context_message_limit: int = 12
    context_message_characters: int = 1200
    sentence_min_characters: int = 24
    sentence_abbreviations: tuple[str, ...] = ()
