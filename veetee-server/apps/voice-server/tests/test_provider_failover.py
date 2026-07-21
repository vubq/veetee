from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
)
from veetee_voice_server.providers.contracts import LlmRequest, LlmTextDelta
from veetee_voice_server.providers.failover import (
    FailoverLlmProvider,
    LlmProviderCandidate,
    ProviderCircuit,
)
from veetee_voice_server.providers.nine_router import NineRouterProviderError

pytestmark = pytest.mark.asyncio


class FakeProvider:
    def __init__(self, *, events: tuple[str, ...] = (), error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.stream_calls = 0
        self.json_calls = 0

    async def complete_json(self, **_: Any) -> dict[str, Any]:
        self.json_calls += 1
        if self.error:
            raise self.error
        return {"provider": self.events[0] if self.events else "ok"}

    async def stream(
        self, request: LlmRequest, context: OperationContext
    ) -> AsyncIterator[LlmTextDelta]:
        del request
        self.stream_calls += 1
        for event in self.events:
            context.checkpoint()
            yield LlmTextDelta(event)
        if self.error:
            raise self.error


def operation_context(token: CancellationToken | None = None) -> OperationContext:
    return OperationContext(
        "session-1",
        "session-1:1",
        1,
        token or CancellationToken(),
        time.monotonic() + 5,
    )


def chain(*providers: FakeProvider) -> FailoverLlmProvider:
    return FailoverLlmProvider(
        tuple(
            LlmProviderCandidate(f"provider-{index}", provider)  # type: ignore[arg-type]
            for index, provider in enumerate(providers)
        )
    )


async def test_retryable_failure_falls_back_before_stream_output() -> None:
    primary = FakeProvider(error=NineRouterProviderError("busy", retryable=True))
    fallback = FakeProvider(events=("fallback",))

    stream = chain(primary, fallback).stream(object(), operation_context())  # type: ignore[arg-type]
    events = [event async for event in stream]

    assert events == [LlmTextDelta("fallback")]
    assert primary.stream_calls == 1
    assert fallback.stream_calls == 1


async def test_chain_never_falls_back_after_visible_output() -> None:
    primary = FakeProvider(
        events=("partial",), error=NineRouterProviderError("lost", retryable=True)
    )
    fallback = FakeProvider(events=("must-not-run",))

    with pytest.raises(NineRouterProviderError):
        _ = [
            event
            async for event in chain(primary, fallback).stream(object(), operation_context())  # type: ignore[arg-type]
        ]

    assert fallback.stream_calls == 0


async def test_cancelled_turn_never_falls_back() -> None:
    token = CancellationToken()
    token.cancel("button_interrupt")
    primary = FakeProvider(error=NineRouterProviderError("busy", retryable=True))
    fallback = FakeProvider(events=("must-not-run",))

    with pytest.raises(TurnCancelledError):
        await chain(primary, fallback).complete_json(
            system_prompt="fixture",
            user_prompt="fixture",
            context=operation_context(token),
        )

    assert primary.json_calls == 0
    assert fallback.json_calls == 0


async def test_open_circuit_skips_primary_until_recovery_window() -> None:
    primary = FakeProvider(error=NineRouterProviderError("busy", retryable=True))
    fallback = FakeProvider(events=("fallback",))
    provider_chain = chain(primary, fallback)

    for _ in range(4):
        result = await provider_chain.complete_json(
            system_prompt="fixture",
            user_prompt="fixture",
            context=operation_context(),
        )
        assert result == {"provider": "fallback"}

    assert primary.json_calls == 3
    assert provider_chain.health["provider-0"] == "open"


async def test_cancelled_half_open_probe_does_not_lock_provider_forever() -> None:
    primary = FakeProvider(error=TurnCancelledError("button_interrupt"))
    candidate = LlmProviderCandidate(
        "primary",
        primary,  # type: ignore[arg-type]
        ProviderCircuit(failures=3, opened_at=time.monotonic() - 31),
    )
    provider_chain = FailoverLlmProvider((candidate,))

    with pytest.raises(TurnCancelledError):
        await provider_chain.complete_json(
            system_prompt="fixture",
            user_prompt="fixture",
            context=operation_context(),
        )

    assert provider_chain.health["primary"] == "open"
    primary.error = None
    primary.events = ("recovered",)
    assert await provider_chain.complete_json(
        system_prompt="fixture",
        user_prompt="fixture",
        context=operation_context(),
    ) == {"provider": "recovered"}
    assert provider_chain.health["primary"] == "closed"


async def test_closed_stream_releases_half_open_probe() -> None:
    primary = FakeProvider(events=("partial", "unused"))
    candidate = LlmProviderCandidate(
        "primary",
        primary,  # type: ignore[arg-type]
        ProviderCircuit(failures=3, opened_at=time.monotonic() - 31),
    )
    provider_chain = FailoverLlmProvider((candidate,))

    stream = provider_chain.stream(object(), operation_context())  # type: ignore[arg-type]
    assert await anext(stream) == LlmTextDelta("partial")
    await stream.aclose()

    assert provider_chain.health["primary"] == "open"
