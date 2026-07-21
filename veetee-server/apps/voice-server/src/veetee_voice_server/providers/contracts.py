from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import (
    AdmissionDecision,
    AudioChunk,
    ConversationPlan,
    Transcript,
)


@dataclass(frozen=True, slots=True)
class LlmRequest:
    transcript: Transcript
    plan: ConversationPlan
    tool_result: Any | None = None
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class LlmTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class LlmToolCallFragment:
    call_id: str | None
    name: str | None
    arguments_fragment: str


@dataclass(frozen=True, slots=True)
class LlmStreamDone:
    finish_reason: str | None


LlmEvent = LlmTextDelta | LlmToolCallFragment | LlmStreamDone


class AdmissionProvider(Protocol):
    async def evaluate(
        self, transcript: Transcript, context: OperationContext
    ) -> AdmissionDecision: ...


class PlannerProvider(Protocol):
    async def plan(
        self, transcript: Transcript, admission: AdmissionDecision, context: OperationContext
    ) -> ConversationPlan: ...


class LlmProvider(Protocol):
    def stream(self, request: LlmRequest, context: OperationContext) -> AsyncIterator[LlmEvent]: ...


class TtsProvider(Protocol):
    def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]: ...


class ToolBroker(Protocol):
    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any: ...
