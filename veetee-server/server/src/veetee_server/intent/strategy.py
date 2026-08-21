"""Abstract intent routing strategies and protocol command fast-path handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class IntentType(StrEnum):
    """Supported intent classification targets."""

    DIRECT_CHAT = "direct_chat"
    FUNCTION_CALL = "function_call"
    INTENT_MODEL = "intent_model"
    PROTOCOL_COMMAND = "protocol_command"


@dataclass(frozen=True, slots=True)
class IntentRoutingContext:
    """Context provided to intent strategies."""

    utterance: str
    available_tools: list[str] = field(default_factory=list)
    session_id: str = ""
    protocol_command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntentRoutingResult:
    """Outcome of intent routing decision."""

    intent: IntentType
    strategy_name: str
    target_tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    confidence: float = 1.0
    fast_path: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AbstractIntentStrategy(Protocol):
    """Protocol interface for intent routing strategies."""

    name: str

    async def route(self, context: IntentRoutingContext) -> IntentRoutingResult:
        """Determines intent for the given routing context."""
        ...


class IntentStrategyNotFoundError(ValueError):
    """Raised when a configured or requested strategy is not registered."""


class DirectChatStrategy:
    """Strategy that bypasses tools and routes directly to LLM conversational response."""

    name: str = "direct_chat"

    async def route(self, context: IntentRoutingContext) -> IntentRoutingResult:
        return IntentRoutingResult(
            intent=IntentType.DIRECT_CHAT,
            strategy_name=self.name,
            confidence=1.0,
            fast_path=False,
        )


class FunctionCallStrategy:
    """Default strategy utilizing native LLM tool/function calling when tools exist."""

    name: str = "function_call"

    async def route(self, context: IntentRoutingContext) -> IntentRoutingResult:
        if not context.available_tools:
            return IntentRoutingResult(
                intent=IntentType.DIRECT_CHAT,
                strategy_name=self.name,
                confidence=1.0,
                fast_path=False,
            )

        return IntentRoutingResult(
            intent=IntentType.FUNCTION_CALL,
            strategy_name=self.name,
            confidence=1.0,
            fast_path=False,
        )


class IntentModelStrategy:
    """Strategy delegating intent classification to a model/classifier callback.

    NO keyword hardcoding is used.
    """

    name: str = "intent_model"

    def __init__(
        self,
        classifier_fn: Any | None = None,
    ) -> None:
        self.classifier_fn = classifier_fn

    async def route(self, context: IntentRoutingContext) -> IntentRoutingResult:
        if self.classifier_fn is not None:
            res = await self.classifier_fn(context)
            if isinstance(res, IntentRoutingResult):
                return res

        # Default fallback when classifier is not bound
        return IntentRoutingResult(
            intent=IntentType.INTENT_MODEL,
            strategy_name=self.name,
            confidence=0.9,
            fast_path=False,
        )


class IntentRouter:
    """Router selecting appropriate strategy without natural language keyword hardcoding."""

    def __init__(
        self,
        default_strategy: str = "function_call",
        custom_strategies: dict[str, AbstractIntentStrategy] | None = None,
    ) -> None:
        self.default_strategy_name = default_strategy
        self.strategies: dict[str, AbstractIntentStrategy] = {
            "direct_chat": DirectChatStrategy(),
            "function_call": FunctionCallStrategy(),
            "intent_model": IntentModelStrategy(),
        }
        if custom_strategies:
            self.strategies.update(custom_strategies)
        if default_strategy not in self.strategies:
            raise IntentStrategyNotFoundError(
                f"Intent strategy '{default_strategy}' is not registered"
            )

    async def route(
        self,
        context: IntentRoutingContext,
        strategy_override: str | None = None,
    ) -> IntentRoutingResult:
        # Deterministic Fast-Path ONLY for protocol system commands (e.g. system control signals)
        if context.protocol_command and context.protocol_command.startswith("__sys_"):
            return IntentRoutingResult(
                intent=IntentType.PROTOCOL_COMMAND,
                strategy_name="protocol_fast_path",
                target_tool_name=context.protocol_command,
                confidence=1.0,
                fast_path=True,
            )

        strategy_name = strategy_override or self.default_strategy_name
        strategy = self.strategies.get(strategy_name)
        if strategy is None:
            raise IntentStrategyNotFoundError(
                f"Intent strategy '{strategy_name}' is not registered"
            )

        return await strategy.route(context)
