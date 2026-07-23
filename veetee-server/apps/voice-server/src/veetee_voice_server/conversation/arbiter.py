from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from math import inf
from time import monotonic

from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.conversation.types import WakeSource


class ConversationState(StrEnum):
    STANDBY = "standby"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSING = "closing"
    CANCELLING = "cancelling"


class InvalidConversationTransitionError(RuntimeError):
    pass


class StaleTurnError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArbiterSnapshot:
    session_id: str
    state: ConversationState
    assistant_gate_open: bool
    generation: int
    current_turn_id: str | None


@dataclass(frozen=True, slots=True)
class AbortReceipt:
    generation: int
    cancelled_turn_id: str | None
    reason: str


class TurnArbiter:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._lock = asyncio.Lock()
        self._state = ConversationState.STANDBY
        self._assistant_gate_open = False
        self._generation = 0
        self._turn_sequence = 0
        self._current: OperationContext | None = None

    @property
    def snapshot(self) -> ArbiterSnapshot:
        return ArbiterSnapshot(
            session_id=self.session_id,
            state=self._state,
            assistant_gate_open=self._assistant_gate_open,
            generation=self._generation,
            current_turn_id=self._current.turn_id if self._current else None,
        )

    async def open_assistant(self, source: WakeSource) -> ArbiterSnapshot:
        async with self._lock:
            if self._current:
                self._current.token.cancel(f"assistant_reopened:{source.value}")
            self._generation += 1
            self._current = None
            self._assistant_gate_open = True
            self._state = ConversationState.LISTENING
            return self.snapshot

    async def begin_turn(self, total_turn_seconds: float) -> OperationContext:
        if total_turn_seconds < 0:
            raise ValueError("total_turn_seconds must be non-negative")
        async with self._lock:
            if not self._assistant_gate_open or self._state is not ConversationState.LISTENING:
                raise InvalidConversationTransitionError(
                    f"Cannot begin turn while state={self._state.value}"
                )
            self._generation += 1
            self._turn_sequence += 1
            context = OperationContext(
                session_id=self.session_id,
                turn_id=f"{self.session_id}:{self._turn_sequence}",
                generation=self._generation,
                token=CancellationToken(),
                deadline_at=(
                    monotonic() + total_turn_seconds
                    if total_turn_seconds > 0
                    else inf
                ),
            )
            self._current = context
            self._state = ConversationState.THINKING
            return context

    async def mark_speaking(self, context: OperationContext) -> None:
        async with self._lock:
            self._ensure_current(context)
            if self._state is not ConversationState.THINKING:
                raise InvalidConversationTransitionError(
                    f"Cannot speak while state={self._state.value}"
                )
            self._state = ConversationState.SPEAKING

    async def complete_turn(self, context: OperationContext) -> bool:
        async with self._lock:
            if not self._is_current(context):
                return False
            self._current = None
            self._state = (
                ConversationState.LISTENING
                if self._assistant_gate_open
                else ConversationState.STANDBY
            )
            return True

    async def abort(self, reason: str) -> AbortReceipt:
        async with self._lock:
            cancelled_turn_id = self._current.turn_id if self._current else None
            if self._current:
                self._current.token.cancel(reason)
            self._generation += 1
            self._current = None
            self._state = ConversationState.CANCELLING
            return AbortReceipt(
                generation=self._generation,
                cancelled_turn_id=cancelled_turn_id,
                reason=reason,
            )

    async def finish_cancellation(self, receipt: AbortReceipt) -> ArbiterSnapshot:
        async with self._lock:
            if self._generation != receipt.generation:
                return self.snapshot
            if self._state is ConversationState.CANCELLING:
                self._state = (
                    ConversationState.LISTENING
                    if self._assistant_gate_open
                    else ConversationState.STANDBY
                )
            return self.snapshot

    async def begin_closing(self) -> ArbiterSnapshot:
        async with self._lock:
            if not self._assistant_gate_open:
                return self.snapshot
            if self._current:
                self._current.token.cancel("session_closing")
                self._current = None
                self._generation += 1
            self._state = ConversationState.CLOSING
            return self.snapshot

    async def close_assistant(self, reason: str) -> ArbiterSnapshot:
        async with self._lock:
            if self._current:
                self._current.token.cancel(reason)
            self._generation += 1
            self._current = None
            self._assistant_gate_open = False
            self._state = ConversationState.STANDBY
            return self.snapshot

    def is_current(self, context: OperationContext) -> bool:
        return self._is_current(context) and not context.token.cancelled

    def require_current(self, context: OperationContext) -> None:
        context.checkpoint()
        self._ensure_current(context)

    def _is_current(self, context: OperationContext) -> bool:
        return (
            self._current is context
            and self._generation == context.generation
            and self._current.generation == context.generation
        )

    def _ensure_current(self, context: OperationContext) -> None:
        if not self._is_current(context):
            raise StaleTurnError(f"Stale turn output rejected: {context.turn_id}")
