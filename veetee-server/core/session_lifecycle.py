"""Lifecycle state for one realtime VeeTee client session.

This module intentionally contains no semantic routing, protocol parsing, ASR,
LLM, TTS, or tool logic. It owns only cancellation/generation invariants so
stale async work can be rejected consistently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionLifecycleState:
    active: bool = True
    closed: bool = False
    turn_generation: int = 0
    capture_generation: int = 0
    wake_generation: int = 0
    current_turn_task: Optional[asyncio.Task] = None
    current_cancel_event: Optional[asyncio.Event] = None

    def owns_turn(self, generation: int) -> bool:
        return self.active and self.turn_generation == int(generation)

    def advance_turn(self) -> int:
        self.turn_generation += 1
        return self.turn_generation

    def advance_capture(self) -> int:
        self.capture_generation += 1
        return self.capture_generation

    def advance_wake(self) -> int:
        self.wake_generation += 1
        return self.wake_generation

    def bind_turn(
        self,
        task: asyncio.Task,
        cancel_event: asyncio.Event,
    ) -> None:
        self.current_turn_task = task
        self.current_cancel_event = cancel_event

    def clear_turn_if_current(self, task: Optional[asyncio.Task] = None) -> bool:
        expected = task if task is not None else asyncio.current_task()
        if self.current_turn_task is not expected:
            return False
        self.current_turn_task = None
        self.current_cancel_event = None
        return True

    def abort_turn(self, *, current_task: Optional[asyncio.Task] = None) -> None:
        """Invalidate the current turn and cancel any independently-running task."""
        self.advance_turn()
        cancel_event = self.current_cancel_event
        self.current_cancel_event = None
        if cancel_event is not None:
            cancel_event.set()

        task = self.current_turn_task
        if (
            task is not None
            and not task.done()
            and task is not (current_task if current_task is not None else asyncio.current_task())
        ):
            task.cancel()
            self.current_turn_task = None

    def begin_close(self) -> bool:
        """Return False when close has already been committed."""
        if self.closed:
            return False
        self.closed = True
        self.active = False
        self.advance_turn()
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        return True
