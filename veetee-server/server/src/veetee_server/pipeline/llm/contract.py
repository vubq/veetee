"""Typed contracts, dataclasses, and protocols for LLM providers."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ChatMessage",
    "CompletedToolCall",
    "LLMCancelledEvent",
    "LLMCompletedEvent",
    "LLMFailedEvent",
    "LLMProvider",
    "LLMResult",
    "LLMStartedEvent",
    "LLMStreamEvent",
    "LLMTextDeltaEvent",
    "LLMToolCallDeltaEvent",
    "LLMUsage",
    "LLMUsageEvent",
    "OmniRouteLLMConfig",
    "TextDelta",
    "ToolCallDelta",
]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Typed OpenAI-compatible chat message."""

    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental text content delta from streaming LLM response."""

    content: str
    index: int = 0
    reasoning_content: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """Incremental tool call delta merged by index."""

    index: int
    id: str | None = None
    type: str | None = None
    name: str | None = None
    arguments_delta: str = ""


@dataclass(frozen=True, slots=True)
class CompletedToolCall:
    """Completed tool call with merged function name and arguments."""

    id: str
    name: str
    arguments: str
    parsed_arguments: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token usage metadata returned by LLM provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Final normalized result of an LLM generation turn."""

    text: str
    tool_calls: list[CompletedToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: LLMUsage | None = None
    raw_response_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMStartedEvent:
    """Event emitted when LLM streaming starts."""

    provider: str
    model: str
    request_id: str
    timestamp_ms: float


@dataclass(frozen=True, slots=True)
class LLMTextDeltaEvent:
    """Event emitted when LLM yields a text token delta."""

    delta: str
    index: int = 0
    reasoning: bool = False


@dataclass(frozen=True, slots=True)
class LLMToolCallDeltaEvent:
    """Event emitted when LLM yields a tool call fragment."""

    index: int
    id: str | None
    name: str | None
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class LLMUsageEvent:
    """Event emitted when LLM yields usage statistics."""

    usage: LLMUsage


@dataclass(frozen=True, slots=True)
class LLMCompletedEvent:
    """Event emitted when LLM generation completes successfully."""

    text: str
    tool_calls: list[CompletedToolCall]
    finish_reason: str | None
    usage: LLMUsage | None


@dataclass(frozen=True, slots=True)
class LLMFailedEvent:
    """Event emitted when LLM generation fails with an error."""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class LLMCancelledEvent:
    """Event emitted when LLM generation stream is cancelled."""

    reason: str = "cancelled"


type LLMStreamEvent = (
    LLMStartedEvent
    | LLMTextDeltaEvent
    | LLMToolCallDeltaEvent
    | LLMUsageEvent
    | LLMCompletedEvent
    | LLMFailedEvent
    | LLMCancelledEvent
)


@dataclass(frozen=True, slots=True)
class OmniRouteLLMConfig:
    """Typed configuration for OmniRoute LLM adapter."""

    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = ""
    model: str = "groq/openai/gpt-oss-120b"
    reasoning_effort: str = "low"
    connect_timeout_seconds: float = 3.0
    first_token_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 30.0
    max_concurrency: int = 4
    admission_timeout_seconds: float = 2.0
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 10.0
    max_response_bytes: int = 1048576

    def __post_init__(self) -> None:
        if not self.base_url or not self.base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if not self.model or not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if self.first_token_timeout_seconds <= 0:
            raise ValueError("first_token_timeout_seconds must be positive")
        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if self.admission_timeout_seconds <= 0:
            raise ValueError("admission_timeout_seconds must be positive")
        if self.circuit_breaker_failure_threshold <= 0:
            raise ValueError("circuit_breaker_failure_threshold must be positive")
        if self.circuit_breaker_cooldown_seconds <= 0:
            raise ValueError("circuit_breaker_cooldown_seconds must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers generating streaming responses."""

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent]:
        """Yields streaming LLM events for the given chat messages."""
        ...
