"""Bounded downlink queue carrying ordered control and audio items.

The gateway produces a single ordered stream toward the device: JSON control
messages (stt/tts events) interleaved with encoded audio frames. One
``DownlinkQueue`` per session preserves that ordering and applies the same
backpressure guarantees as the M1.5 audio queues:

- byte / item / duration bounds with ``drop_oldest``, ``reject_new`` or
  ``fail_session`` overflow policy;
- generation tagging so an aborted or replaced turn is purged instantly;
- cancellation-aware blocking ``get`` and ``QueueClosedError`` on close.

Items belong to a generation when they are enqueued. ``put`` is atomic with
respect to ``set_generation`` (it does not await while holding the condition
lock), which is what makes the abort purge race-free: an in-flight item is
either enqueued with the pre-abort generation and purged by the bump, or the
enqueue is cancelled at the lock acquisition before it can complete.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from veetee_server.audio.queue import (
    OverflowPolicy,
    QueueClosedError,
    SlowClientQueueOverflowError,
)


class DownlinkKind(StrEnum):
    """Kind of payload carried by a downlink item."""

    CONTROL = "control"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class DownlinkItem:
    """Typed ordered item stored in a downlink queue."""

    kind: DownlinkKind
    payload: bytes
    generation: int = 0
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("Downlink payload must not be empty")
        if self.generation < 0:
            raise ValueError("Downlink generation must be non-negative")
        if self.kind is DownlinkKind.AUDIO and self.duration_ms <= 0:
            raise ValueError("Downlink audio duration must be positive")

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


class DownlinkQueue:
    """Bounded FIFO queue for ordered downlink control and audio items."""

    def __init__(
        self,
        max_items: int = 100,
        max_bytes: int = 1048576,
        max_duration_ms: float = 10000.0,
        overflow_policy: OverflowPolicy = OverflowPolicy.FAIL_SESSION,
    ) -> None:
        if max_items <= 0 or max_bytes <= 0 or max_duration_ms <= 0:
            raise ValueError(
                "Queue limits (max_items, max_bytes, max_duration_ms) must be positive"
            )

        self.max_items = max_items
        self.max_bytes = max_bytes
        self.max_duration_ms = max_duration_ms
        self.overflow_policy = overflow_policy

        self._queue: deque[DownlinkItem] = deque()
        self._total_bytes: int = 0
        self._total_duration_ms: float = 0.0
        self._generation: int = 0
        self._closed: bool = False

        self._cond = asyncio.Condition()

    @property
    def item_count(self) -> int:
        return len(self._queue)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def total_duration_ms(self) -> float:
        return self._total_duration_ms

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def is_closed(self) -> bool:
        return self._closed

    def set_generation(self, generation: int) -> None:
        """Updates the active generation and immediately purges stale items.

        Items are enqueued in FIFO order with non-decreasing generations, so
        every item belonging to an older generation sits at the front of the
        queue; popping front-while-stale removes all of them.
        """
        if generation < self._generation:
            raise ValueError(
                f"Generation cannot regress: current {self._generation}, requested {generation}"
            )
        self._generation = generation
        while self._queue and self._queue[0].generation < self._generation:
            self._pop_oldest_internal()

    def _would_overflow(self, item: DownlinkItem) -> bool:
        return (
            len(self._queue) + 1 > self.max_items
            or self._total_bytes + item.size_bytes > self.max_bytes
            or self._total_duration_ms + item.duration_ms > self.max_duration_ms
        )

    def _pop_oldest_internal(self) -> DownlinkItem | None:
        if not self._queue:
            return None
        oldest = self._queue.popleft()
        self._total_bytes -= oldest.size_bytes
        self._total_duration_ms -= oldest.duration_ms
        return oldest

    async def put(self, item: DownlinkItem) -> bool:
        """Puts an item into the queue according to the overflow policy.

        Returns True if accepted, False if dropped or rejected.
        Raises SlowClientQueueOverflowError under FAIL_SESSION policy.
        """
        async with self._cond:
            if self._closed:
                raise QueueClosedError("Cannot put item into a closed queue")

            # Drop stale item immediately without queuing
            if item.generation < self._generation:
                return False
            if item.generation > self._generation:
                raise ValueError(
                    f"Item generation {item.generation} is ahead of queue generation "
                    f"{self._generation}"
                )

            # Single item larger than total queue capacity
            if item.size_bytes > self.max_bytes or item.duration_ms > self.max_duration_ms:
                if self.overflow_policy == OverflowPolicy.FAIL_SESSION:
                    raise SlowClientQueueOverflowError(
                        "Single item exceeds total queue max capacity"
                    )
                return False

            while self._would_overflow(item):
                if self.overflow_policy == OverflowPolicy.DROP_OLDEST:
                    popped = self._pop_oldest_internal()
                    if popped is None:
                        return False
                elif self.overflow_policy == OverflowPolicy.REJECT_NEW:
                    return False
                elif self.overflow_policy == OverflowPolicy.FAIL_SESSION:
                    raise SlowClientQueueOverflowError(
                        "Queue limit exceeded "
                        f"(items={len(self._queue)}, bytes={self._total_bytes}, "
                        f"duration={self._total_duration_ms}ms)"
                    )

            self._queue.append(item)
            self._total_bytes += item.size_bytes
            self._total_duration_ms += item.duration_ms
            self._cond.notify()
            return True

    async def get(self) -> DownlinkItem:
        """Retrieves the next valid (non-stale) item from the queue."""
        async with self._cond:
            while True:
                if self._closed and not self._queue:
                    raise QueueClosedError("Cannot get item from an empty closed queue")

                while not self._queue and not self._closed:
                    await self._cond.wait()

                if self._closed and not self._queue:
                    raise QueueClosedError("Queue closed while waiting for item")

                item = self._pop_oldest_internal()
                if item is None:
                    continue

                # Filter out stale generation items automatically
                if item.generation < self._generation:
                    continue

                return item

    def drain(self) -> list[DownlinkItem]:
        """Synchronously drains all valid (non-stale) items from the queue."""
        items: list[DownlinkItem] = []
        while self._queue:
            item = self._pop_oldest_internal()
            if item is not None and item.generation >= self._generation:
                items.append(item)
        return items

    def close(self) -> None:
        """Closes the queue and wakes up any pending waiters."""
        self._closed = True
        self._queue.clear()
        self._total_bytes = 0
        self._total_duration_ms = 0.0
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._notify_all())
        except RuntimeError:
            pass

    async def _notify_all(self) -> None:
        async with self._cond:
            self._cond.notify_all()
