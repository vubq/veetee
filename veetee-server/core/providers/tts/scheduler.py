from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


PRIORITY_VALUES = {
    "live": 0,
    "dashboard": 10,
    "prewarm": 20,
}


@dataclass
class _Waiter:
    priority_name: str
    base_priority: int
    sequence: int
    enqueued_at: float


class TTSLease:
    def __init__(self, scheduler: "TTSAdmissionScheduler", *, priority: str, wait_ms: float):
        self._scheduler = scheduler
        self.priority = priority
        self.wait_ms = wait_ms
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._scheduler.release()


class TTSAdmissionScheduler:
    """Single-engine admission with live/dashboard/prewarm priority and aging."""

    def __init__(self, *, aging_priority_per_second: float = 2.0):
        self._condition = asyncio.Condition()
        self._waiters: list[_Waiter] = []
        self._active = False
        self._active_priority: Optional[str] = None
        self._sequence = 0
        self._aging_priority_per_second = max(0.1, float(aging_priority_per_second))

    def _priority_value(self, name: str) -> int:
        return PRIORITY_VALUES.get(str(name or "live").strip().lower(), PRIORITY_VALUES["live"])

    def _select_next(self, now: float) -> Optional[_Waiter]:
        if not self._waiters:
            return None

        def rank(waiter: _Waiter):
            waited = max(0.0, now - waiter.enqueued_at)
            effective = waiter.base_priority - waited * self._aging_priority_per_second
            return (effective, waiter.sequence)

        return min(self._waiters, key=rank)

    async def acquire(
        self,
        priority: str,
        *,
        cancel_event: Optional[asyncio.Event] = None,
        deadline_seconds: Optional[float] = None,
    ) -> TTSLease:
        priority_name = str(priority or "live").strip().lower()
        if priority_name not in PRIORITY_VALUES:
            priority_name = "live"
        enqueued_at = time.perf_counter()
        deadline_at = None
        if deadline_seconds is not None:
            deadline_at = enqueued_at + max(0.001, float(deadline_seconds))

        async with self._condition:
            self._sequence += 1
            waiter = _Waiter(
                priority_name=priority_name,
                base_priority=self._priority_value(priority_name),
                sequence=self._sequence,
                enqueued_at=enqueued_at,
            )
            self._waiters.append(waiter)
            try:
                while True:
                    now = time.perf_counter()
                    if cancel_event is not None and cancel_event.is_set():
                        if waiter in self._waiters:
                            self._waiters.remove(waiter)
                        self._condition.notify_all()
                        raise asyncio.CancelledError
                    if deadline_at is not None and now >= deadline_at:
                        if waiter in self._waiters:
                            self._waiters.remove(waiter)
                        self._condition.notify_all()
                        raise asyncio.TimeoutError("TTS admission deadline exceeded")

                    selected = self._select_next(now)
                    if not self._active and selected is waiter:
                        self._waiters.remove(waiter)
                        self._active = True
                        self._active_priority = priority_name
                        return TTSLease(
                            self,
                            priority=priority_name,
                            wait_ms=(now - enqueued_at) * 1000.0,
                        )

                    poll_seconds = 0.05 if cancel_event is not None or deadline_at is not None else 1.0
                    if deadline_at is not None:
                        poll_seconds = min(poll_seconds, max(0.001, deadline_at - now))
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=poll_seconds)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                    self._condition.notify_all()
                raise

    async def release(self) -> None:
        async with self._condition:
            self._active = False
            self._active_priority = None
            self._condition.notify_all()

    def snapshot(self) -> dict:
        now = time.perf_counter()
        waiting = {name: 0 for name in PRIORITY_VALUES}
        longest_wait_ms = 0.0
        for waiter in self._waiters:
            waiting[waiter.priority_name] = waiting.get(waiter.priority_name, 0) + 1
            longest_wait_ms = max(longest_wait_ms, (now - waiter.enqueued_at) * 1000.0)
        return {
            "active": self._active,
            "active_priority": self._active_priority,
            "waiting": waiting,
            "longest_wait_ms": round(longest_wait_ms, 3),
        }
