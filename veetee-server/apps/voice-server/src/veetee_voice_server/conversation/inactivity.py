from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic

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
        max_session_seconds: float,
        goodbye: GoodbyeCallback,
    ) -> None:
        self._arbiter = arbiter
        self._first_input_seconds = first_input_seconds
        self._between_turns_seconds = between_turns_seconds
        self._closing_grace_seconds = closing_grace_seconds
        self._max_session_seconds = max_session_seconds
        self._goodbye = goodbye
        self._lock = asyncio.Lock()
        self._expiry_lock = asyncio.Lock()
        self._timer: asyncio.Task[None] | None = None
        self._session_timer: asyncio.Task[None] | None = None
        self._deadline_at: float | None = None
        self._deadline_reason: str | None = None

    async def assistant_opened(self, source: WakeSource) -> None:
        start_session_ceiling = not self._arbiter.snapshot.assistant_gate_open
        await self._cancel_timer()
        await self._arbiter.open_assistant(source)
        if start_session_ceiling:
            await self._arm_session_limit()
        await self._arm(self._first_input_seconds, "first_input_timeout")

    async def valid_user_activity(self) -> None:
        await self._cancel_timer()
        self._deadline_at = None
        self._deadline_reason = None

    async def candidate_started(self) -> None:
        """Pause timeout evaluation without treating raw speech as valid activity."""
        await self._cancel_timer()

    async def candidate_rejected(self) -> None:
        if self._arbiter.snapshot.state is ConversationState.LISTENING:
            await self._resume_deadline()

    async def turn_completed(self) -> None:
        if self._arbiter.snapshot.state is ConversationState.LISTENING:
            await self._arm(self._between_turns_seconds, "between_turns_timeout")

    async def wake_during_closing(self, source: WakeSource) -> None:
        await self._cancel_timer()
        await self._arbiter.open_assistant(source)
        await self._arm_session_limit()
        await self._arm(self._first_input_seconds, "first_input_timeout")

    async def interrupt_closing(self) -> None:
        """Cancel goodbye/grace without resetting the absolute session ceiling."""
        await self._cancel_timer()
        self._deadline_at = None
        self._deadline_reason = None

    async def assistant_closed(self, reason: str) -> None:
        await self._cancel_timer()
        await self._cancel_session_timer()
        self._deadline_at = None
        self._deadline_reason = None
        await self._arbiter.close_assistant(reason)

    async def close(self) -> None:
        await self._cancel_timer()
        await self._cancel_session_timer()
        self._deadline_at = None
        self._deadline_reason = None
        await self._arbiter.close_assistant("controller_closed")

    async def _arm(self, delay: float, reason: str) -> None:
        if delay <= 0:
            raise ValueError("inactivity delay must be positive")
        await self._cancel_timer()
        self._deadline_at = monotonic() + delay
        self._deadline_reason = reason
        async with self._lock:
            self._timer = asyncio.create_task(self._run(delay, reason))

    async def _arm_session_limit(self) -> None:
        if self._max_session_seconds <= 0:
            raise ValueError("max_session_seconds must be positive")
        await self._cancel_session_timer()
        async with self._lock:
            self._session_timer = asyncio.create_task(self._run_session_limit())

    async def _resume_deadline(self) -> None:
        deadline = self._deadline_at
        reason = self._deadline_reason
        if deadline is None or reason is None:
            return
        await self._cancel_timer()
        delay = max(0.0, deadline - monotonic())
        async with self._lock:
            self._timer = asyncio.create_task(self._run(delay, reason))

    async def _run(self, delay: float, reason: str) -> None:
        try:
            await asyncio.sleep(delay)
            await self._expire(reason, require_listening=True)
        except asyncio.CancelledError:
            raise

    async def _run_session_limit(self) -> None:
        try:
            await asyncio.sleep(self._max_session_seconds)
            await self._expire("max_session_duration", require_listening=False)
        except asyncio.CancelledError:
            raise

    async def _expire(self, reason: str, *, require_listening: bool) -> None:
        async with self._expiry_lock:
            snapshot = self._arbiter.snapshot
            if not snapshot.assistant_gate_open:
                return
            if require_listening and snapshot.state is not ConversationState.LISTENING:
                return
            if snapshot.state is ConversationState.CLOSING:
                return
            self._deadline_at = None
            self._deadline_reason = None
            if not require_listening:
                await self._cancel_timer()
            await self._arbiter.begin_closing()
            await self._goodbye(reason)
            await asyncio.sleep(self._closing_grace_seconds)
            if self._arbiter.snapshot.state is ConversationState.CLOSING:
                await self._arbiter.close_assistant(reason)
                await self._cancel_session_timer()

    async def _cancel_timer(self) -> None:
        async with self._lock:
            timer = self._timer
            self._timer = None
        if timer and not timer.done():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    async def _cancel_session_timer(self) -> None:
        async with self._lock:
            timer = self._session_timer
            self._session_timer = None
        if timer and timer is not asyncio.current_task() and not timer.done():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
