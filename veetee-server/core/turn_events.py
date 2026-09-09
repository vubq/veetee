from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Union


class TurnEventType(str, Enum):
    CONTROL = "control"
    SPEECH = "speech"
    MEMORY = "memory"
    CONFIRMATION = "confirmation"
    TOOL_CALL = "tool_call"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ControlEvent:
    intent: str = "chat"
    lifecycle: str = "continue"
    emotion: str = "neutral"
    type: TurnEventType = field(default=TurnEventType.CONTROL, init=False)


@dataclass(frozen=True)
class SpeechSegmentEvent:
    text: str
    emotion: Optional[str] = None
    type: TurnEventType = field(default=TurnEventType.SPEECH, init=False)


@dataclass(frozen=True)
class MemoryProposalEvent:
    call_id: str
    action: str
    value: str = ""
    fact_id: str = ""
    revision: Optional[int] = None
    evidence: str = ""
    type: TurnEventType = field(default=TurnEventType.MEMORY, init=False)


@dataclass(frozen=True)
class ConfirmationDecisionEvent:
    call_id: str
    action_id: str
    decision: str
    type: TurnEventType = field(default=TurnEventType.CONFIRMATION, init=False)


@dataclass(frozen=True)
class ToolCallReadyEvent:
    call_id: str
    name: str
    arguments: Dict[str, Any]
    type: TurnEventType = field(default=TurnEventType.TOOL_CALL, init=False)


@dataclass(frozen=True)
class CompletedEvent:
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    type: TurnEventType = field(default=TurnEventType.COMPLETED, init=False)


@dataclass(frozen=True)
class FailedEvent:
    error: str
    type: TurnEventType = field(default=TurnEventType.FAILED, init=False)


TurnEvent = Union[
    ControlEvent,
    SpeechSegmentEvent,
    MemoryProposalEvent,
    ConfirmationDecisionEvent,
    ToolCallReadyEvent,
    CompletedEvent,
    FailedEvent,
]
