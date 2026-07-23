from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from veetee_voice_server.conversation.cancellation import (
    OperationContext,
    OperationDeadlineExceededError,
    TurnCancelledError,
)
from veetee_voice_server.providers.contracts import LlmEvent, LlmRequest
from veetee_voice_server.providers.nine_router import (
    NineRouterLlmProvider,
    NineRouterProviderError,
)


class ProviderChainUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class ProviderCircuit:
    failure_threshold: int = 3
    recovery_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    half_open: bool = False

    def allow_request(self, now: float) -> bool:
        if self.opened_at is None:
            return True
        if now - self.opened_at < self.recovery_seconds:
            return False
        if self.half_open:
            return False
        self.half_open = True
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.half_open = False

    def record_failure(self, now: float) -> None:
        self.half_open = False
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = now

    def release_probe(self) -> None:
        """Release a half-open trial that ended for a non-provider reason."""
        self.half_open = False

    @property
    def state(self) -> str:
        if self.half_open:
            return "half_open"
        return "open" if self.opened_at is not None else "closed"


@dataclass(slots=True)
class LlmProviderCandidate:
    provider_id: str
    provider: NineRouterLlmProvider
    circuit: ProviderCircuit = field(default_factory=ProviderCircuit)


class FailoverLlmProvider:
    """Ordered LLM chain that never falls back after output becomes user-visible."""

    def __init__(self, candidates: tuple[LlmProviderCandidate, ...]) -> None:
        if not candidates:
            raise ValueError("LLM failover chain requires at least one provider")
        self._candidates = candidates

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        context: OperationContext,
        schema: Mapping[str, Any] | None = None,
        schema_name: str = "veetee_return_json",
        schema_transport: Literal["tool_call", "json_object", "json_schema"] = "tool_call",
        max_output_tokens: int | None = None,
        validate_schema: bool = True,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        attempted = False
        for candidate in self._candidates:
            context.checkpoint()
            if not candidate.circuit.allow_request(time.monotonic()):
                continue
            attempted = True
            half_open_probe = candidate.circuit.half_open
            try:
                value = await candidate.provider.complete_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    context=context,
                    schema=schema,
                    schema_name=schema_name,
                    schema_transport=schema_transport,
                    max_output_tokens=max_output_tokens,
                    validate_schema=validate_schema,
                )
            except (TurnCancelledError, OperationDeadlineExceededError):
                raise
            except Exception as error:
                candidate.circuit.record_failure(time.monotonic())
                last_error = error
                if not _is_retryable(error):
                    raise
                continue
            else:
                candidate.circuit.record_success()
                return value
            finally:
                if half_open_probe and candidate.circuit.half_open:
                    candidate.circuit.release_probe()
        if last_error is not None:
            raise last_error
        state = "all circuits open" if not attempted else "no provider completed"
        raise ProviderChainUnavailableError(f"LLM provider chain unavailable: {state}")

    async def prewarm(self, context: OperationContext) -> None:
        result = await self.complete_json(
            system_prompt='Return only a JSON object with {"ready":true}.',
            user_prompt="Warm the configured model for a latency-sensitive voice session.",
            context=context,
        )
        if result.get("ready") is not True:
            raise ProviderChainUnavailableError("LLM chain prewarm returned an invalid response")

    async def stream(
        self, request: LlmRequest, context: OperationContext
    ) -> AsyncIterator[LlmEvent]:
        last_error: Exception | None = None
        attempted = False
        for candidate in self._candidates:
            context.checkpoint()
            if not candidate.circuit.allow_request(time.monotonic()):
                continue
            attempted = True
            half_open_probe = candidate.circuit.half_open
            emitted = False
            try:
                async for event in candidate.provider.stream(request, context):
                    emitted = True
                    yield event
            except (TurnCancelledError, OperationDeadlineExceededError):
                raise
            except Exception as error:
                candidate.circuit.record_failure(time.monotonic())
                last_error = error
                if emitted or not _is_retryable(error):
                    raise
                continue
            else:
                candidate.circuit.record_success()
                return
            finally:
                if half_open_probe and candidate.circuit.half_open:
                    candidate.circuit.release_probe()
        if last_error is not None:
            raise last_error
        state = "all circuits open" if not attempted else "no provider completed"
        raise ProviderChainUnavailableError(f"LLM provider chain unavailable: {state}")

    @property
    def health(self) -> dict[str, str]:
        return {
            candidate.provider_id: candidate.circuit.state for candidate in self._candidates
        }

    async def check_health(self, context: OperationContext) -> bool:
        healthy = False
        for candidate in self._candidates:
            context.checkpoint()
            if not candidate.circuit.allow_request(time.monotonic()):
                continue
            half_open_probe = candidate.circuit.half_open
            try:
                provider_healthy = await candidate.provider.health(context)
            except (TurnCancelledError, OperationDeadlineExceededError):
                raise
            except Exception:
                candidate.circuit.record_failure(time.monotonic())
                continue
            else:
                if provider_healthy:
                    candidate.circuit.record_success()
                    healthy = True
                else:
                    candidate.circuit.record_failure(time.monotonic())
            finally:
                if half_open_probe and candidate.circuit.half_open:
                    candidate.circuit.release_probe()
        return healthy


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, NineRouterProviderError):
        return error.retryable
    return isinstance(error, (httpx.TimeoutException, httpx.NetworkError))
