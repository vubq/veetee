from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from veetee_voice_server.conversation.arbiter import ConversationState, TurnArbiter
from veetee_voice_server.conversation.types import WakeSource

GoodbyeCallback = Callable[[str], Awaitable[None]]


class InactivityController:
    """Owns user-activity timers; raw VAD/rejected audio must not call mark_activity."""

    def __init__(
        self,
        *,
        arbiter: TurnArbiter,
        first_input_seconds: float,
        between_turns_seconds: float,
        closing_grace_seconds: float,
        goodbye: GoodbyeCallback,
    ) -> None:
        self._arbiter = arbiter
        self._first_input_seconds = first_input_seconds
        self._between_turns_seconds = between_turns_seconds
        self._closing_grace_seconds = closing_grace_seconds
        self._goodbye = goodbye
        self._lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None

    async def assistant_opened(self, source: WakeSource) -> None:
        await self._cancel_timer()
        await self._arbiter.open_assistant(source)
        await self._arm(self._first_input_seconds, "first_input_timeout")

    async def valid_user_activity(self) -> None:
        await self._cancel_timer()

    async def turn_completed(self) -> None:
        if self._arbiter.snapshot.state is ConversationState.LISTENING:
            await self._arm(self._between_turns_seconds, "between_turns_timeout")

    async def wake_during_closing(self, source: WakeSource) -> None:
        await self._cancel_timer()
        await self._arbiter.open_assistant(source)
        await self._arm(self._first_input_seconds, "first_input_timeout")

    async def close(self) -> None:
        await self._cancel_timer()
        await self._arbiter.close_assistant("controller_closed")

    async def _arm(self, delay: float, reason: str) -> None:
        if delay <= 0:
            raise ValueError("inactivity delay must be positive")
        await self._cancel_timer()
        async with self._lock:
            self._timer = asyncio.create_task(self._run(delay, reason))

    async def _run(self, delay: float, reason: str) -> None:
        try:
            await asyncio.sleep(delay)
            if self._arbiter.snapshot.state is not ConversationState.LISTENING:
                return
            await self._arbiter.begin_closing()
            await self._goodbye(reason)
            await asyncio.sleep(self._closing_grace_seconds)
            if self._arbiter.snapshot.state.value == ConversationState.CLOSING.value:
                await self._arbiter.close_assistant(reason)
        except asyncio.CancelledError:
            raise

    async def _cancel_timer(self) -> None:
        async with self._lock:
            timer = self._timer
            self._timer = None
        if timer and not timer.done():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
