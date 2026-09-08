import asyncio
import time
from typing import Callable, Optional


class AudioPacer:
    """Bound how far server audio is sent ahead of estimated playback time."""

    def __init__(
        self,
        frame_duration_ms: int,
        send_ahead_ms: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.frame_duration_s = max(1, int(frame_duration_ms)) / 1000.0
        self.send_ahead_s = max(self.frame_duration_s, int(send_ahead_ms) / 1000.0)
        self._clock = clock
        self.playback_end: Optional[float] = None
        self.total_audio_ms = 0.0
        self.total_wait_ms = 0.0
        self.max_estimated_lead_ms = 0.0

    def _normalize_playback_end(self, now: float) -> float:
        if self.playback_end is None or self.playback_end < now:
            self.playback_end = now
        return self.playback_end

    def next_wait_seconds(self) -> float:
        """Return pacing delay needed before sending one more frame."""
        now = self._clock()
        playback_end = self._normalize_playback_end(now)
        projected_end = playback_end + self.frame_duration_s
        delay = max(0.0, projected_end - now - self.send_ahead_s)
        return 0.0 if delay < 1e-6 else delay

    async def wait_for_send(self, cancel_event: asyncio.Event) -> bool:
        """Wait for pacing budget; return False when cancellation wins."""
        delay = self.next_wait_seconds()
        if cancel_event.is_set():
            return False
        if delay <= 0:
            return True

        started = self._clock()
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
            return False
        except asyncio.TimeoutError:
            self.total_wait_ms += max(0.0, (self._clock() - started) * 1000.0)
            return not cancel_event.is_set()

    def record_frame_sent(self) -> float:
        """Advance the estimated playback tail and return lead in milliseconds."""
        now = self._clock()
        playback_end = self._normalize_playback_end(now)
        self.playback_end = playback_end + self.frame_duration_s
        self.total_audio_ms += self.frame_duration_s * 1000.0
        lead_ms = max(0.0, (self.playback_end - now) * 1000.0)
        self.max_estimated_lead_ms = max(self.max_estimated_lead_ms, lead_ms)
        return lead_ms

    def estimated_lead_ms(self) -> float:
        if self.playback_end is None:
            return 0.0
        return max(0.0, (self.playback_end - self._clock()) * 1000.0)
