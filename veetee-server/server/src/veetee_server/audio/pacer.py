"""Monotonic clock audio packet pacer with drift prevention and cancellation awareness."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .protocol import AudioError

# Minimum meaningful sleep duration in seconds; anything below this is treated
# as zero to avoid float-precision no-op sleeps.
_MIN_SLEEP_SECONDS = 1e-6


class PacerError(AudioError):
    """Base exception for audio pacer errors."""


@dataclass(frozen=True, slots=True)
class PacerMetrics:
    frames: int
    drift_resets: int
    max_lag_seconds: float


class PacketPacer:
    """Paces downlink audio packets based on monotonic clock time without accumulating drift.

    Features:
    - Monotonic time tracking.
    - Prevents negative sleep.
    - Prevents accumulation of drift under backpressure or delays by resetting timing anchor.
    - Injectable clock and sleeper for deterministic testing with fake clocks.
    """

    def __init__(
        self,
        max_drift_seconds: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        if max_drift_seconds <= 0:
            raise ValueError("max_drift_seconds must be positive")

        self.max_drift_seconds = max_drift_seconds
        self.clock = clock
        self.sleeper = sleeper or asyncio.sleep

        self._next_send_time: float | None = None
        self._reset_event = asyncio.Event()
        self._frames = 0
        self._drift_resets = 0
        self._max_lag_seconds = 0.0

    @property
    def metrics(self) -> PacerMetrics:
        return PacerMetrics(self._frames, self._drift_resets, self._max_lag_seconds)

    def reset(self) -> None:
        """Resets pacing anchor state."""
        self._next_send_time = None
        self._reset_event.set()
        self._reset_event = asyncio.Event()

    async def pace(self, frame_duration_seconds: float) -> float:
        """Paces next frame based on duration in seconds.

        Returns actual sleep duration executed (in seconds).
        Raises asyncio.CancelledError if sleeping task is cancelled.
        """
        if frame_duration_seconds < 0:
            raise ValueError("frame_duration_seconds cannot be negative")
        if frame_duration_seconds == 0:
            return 0.0

        now = self.clock()
        self._frames += 1

        if self._next_send_time is None:
            self._next_send_time = now + frame_duration_seconds
            return 0.0

        target_time = self._next_send_time
        sleep_time = target_time - now
        self._max_lag_seconds = max(self._max_lag_seconds, max(0.0, -sleep_time))

        # Clamp sub-microsecond sleeps to zero: float accumulation across a long
        # stream can produce meaningless positive deltas (e.g. 5.5e-17 s) that
        # would otherwise trigger a no-op sleep every frame.
        if 0.0 < sleep_time < _MIN_SLEEP_SECONDS:
            sleep_time = 0.0

        if sleep_time < -self.max_drift_seconds:
            # Accumulated drift exceeds max limit -> reset anchor to now
            self._next_send_time = now + frame_duration_seconds
            self._drift_resets += 1
            sleep_time = 0.0
        elif sleep_time < 0.0:
            # Slightly behind schedule -> no sleep, schedule next frame relative to target
            sleep_time = 0.0
            self._next_send_time = target_time + frame_duration_seconds
        else:
            self._next_send_time = target_time + frame_duration_seconds

        if sleep_time > 0.0:
            reset_event = self._reset_event
            sleep_task = asyncio.ensure_future(self.sleeper(sleep_time))
            reset_task = asyncio.create_task(reset_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {sleep_task, reset_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if reset_task in done:
                    sleep_task.cancel()
                    await asyncio.gather(sleep_task, return_exceptions=True)
                    return 0.0
                await sleep_task
            finally:
                if not sleep_task.done():
                    sleep_task.cancel()
                reset_task.cancel()
                await asyncio.gather(sleep_task, reset_task, return_exceptions=True)

        return max(0.0, sleep_time)
