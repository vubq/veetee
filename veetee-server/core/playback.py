"""Single-writer ownership for stock Xiaozhi playback.

The stock protocol exposes one TTS/audio playback channel but no server-side
queue-depth or flush acknowledgement.  This coordinator therefore protects
the server side of that channel: every producer owns a short-lived lease and
must stop writing as soon as another producer takes ownership.

It deliberately knows nothing about user language or semantic intent.  The
session/tool layer decides *why* a producer should start; this module only
enforces lifecycle/generation invariants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PlaybackLease:
    owner: str
    generation: int
    revision: int


class PlaybackCoordinator:
    """Track the single producer currently allowed to write playback audio."""

    def __init__(self) -> None:
        self._revision = 0
        self._active: Optional[PlaybackLease] = None

    def claim(self, owner: str, generation: int) -> PlaybackLease:
        name = str(owner or "").strip()
        if not name:
            raise ValueError("playback owner must not be empty")
        self._revision += 1
        lease = PlaybackLease(name, int(generation), self._revision)
        self._active = lease
        return lease

    def is_current(self, lease: Optional[PlaybackLease]) -> bool:
        return lease is not None and lease == self._active

    def release(self, lease: Optional[PlaybackLease]) -> bool:
        if not self.is_current(lease):
            return False
        self._active = None
        return True

    def invalidate(self, owner: Optional[str] = None) -> bool:
        """Invalidate the active lease, optionally only for one producer."""
        active = self._active
        if active is None:
            return False
        if owner is not None and active.owner != owner:
            return False
        self._revision += 1
        self._active = None
        return True

    @property
    def active(self) -> Optional[PlaybackLease]:
        return self._active

    def snapshot(self) -> dict:
        active = self._active
        return {
            "revision": self._revision,
            "owner": active.owner if active else "",
            "generation": active.generation if active else None,
            "active": active is not None,
        }
