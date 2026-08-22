"""Default-deny integration permission gate with bounded sliding-window rate limits.

Decision (locked, M6.6): every external integration action is denied unless a
persisted per-agent permission row explicitly allows it. The permission lookup
is fail-closed: no repository or no matching row means "deny". Rate limiting
uses an in-memory sliding window whose tracked key set is bounded so abusive
or broken callers cannot grow server memory without limit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


class IntegrationPermissionError(Exception):
    """Raised when the default-deny gate rejects an integration action."""


class IntegrationRateLimitError(Exception):
    """Raised when the per-agent sliding-window quota is exhausted."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__(
            f"Integration rate limit exceeded; retry after {retry_after_seconds:.3f}s"
        )
        self.retry_after_seconds = max(float(retry_after_seconds), 0.0)


IntegrationAction = Literal["list", "call"]


@dataclass(frozen=True, slots=True)
class IntegrationPermissionSnapshot:
    """Typed view of one persisted per-agent permission row."""

    can_list: bool = False
    can_call: bool = False
    rate_limit_calls: int = 30
    rate_limit_window_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class RateDecision:
    """Outcome of one sliding-window acquisition attempt."""

    allowed: bool
    retry_after_seconds: float


PermissionLookup = Callable[[str, str, str], IntegrationPermissionSnapshot | None]
"""Resolves (owner_user_id, agent_id, endpoint_id) to the permission snapshot.

Returning ``None`` must mean deny. The triple is tenant-scoped by construction
and matches one persisted per-agent permission row.
"""


def _default_clock() -> float:
    return time.monotonic()


class SlidingWindowRateLimiter:
    """Bounded in-memory sliding-window rate limiter.

    Each key keeps at most ``max_calls`` timestamps inside its window; the
    total number of tracked keys is capped at ``max_tracked_keys`` so memory
    usage cannot grow unbounded under random-key floods.
    """

    def __init__(
        self,
        *,
        max_tracked_keys: int = 8192,
        clock: Callable[[], float] = _default_clock,
    ) -> None:
        if max_tracked_keys <= 0:
            raise ValueError("max_tracked_keys must be positive")
        self._max_tracked_keys = max_tracked_keys
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def try_acquire(
        self, key: str, *, max_calls: int, window_seconds: float
    ) -> RateDecision:
        """Records one attempt and reports whether it fits inside the window."""
        if max_calls <= 0 or window_seconds <= 0:
            # A non-positive quota denies everything instead of allowing all.
            return RateDecision(allowed=False, retry_after_seconds=0.0)
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_tracked_keys:
                    self._evict_locked(cutoff)
                if len(self._events) >= self._max_tracked_keys:
                    # Every slot holds live traffic; refuse new keys to keep
                    # the limiter bounded (fail-closed for untracked keys).
                    return RateDecision(
                        allowed=False, retry_after_seconds=window_seconds
                    )
                events = deque()
                self._events[key] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max_calls:
                oldest = events[0]
                return RateDecision(
                    allowed=False,
                    retry_after_seconds=max(oldest + window_seconds - now, 0.0),
                )
            events.append(now)
            return RateDecision(allowed=True, retry_after_seconds=0.0)

    def reset(self) -> None:
        """Clears all tracked windows (test and admin convenience)."""
        with self._lock:
            self._events.clear()

    def _evict_locked(self, cutoff: float) -> None:
        stale = [
            key
            for key, events in self._events.items()
            if not events or events[-1] <= cutoff
        ]
        for key in stale:
            del self._events[key]


class IntegrationGate:
    """Default-deny authorization boundary in front of external integrations.

    ``permissions`` may be ``None`` (persistence disabled): every action is
    denied. The gate also refuses disabled endpoints before consulting the
    permission store, then enforces the per-agent sliding-window quota.
    """

    def __init__(
        self,
        permissions: PermissionLookup | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        self._permissions = permissions
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter()

    def authorize(
        self,
        owner_user_id: str,
        agent_id: str,
        endpoint_id: str,
        action: IntegrationAction,
        *,
        endpoint_enabled: bool = True,
    ) -> IntegrationPermissionSnapshot:
        """Returns the granted snapshot or raises a typed denial error."""
        if not endpoint_enabled:
            raise IntegrationPermissionError("External endpoint is disabled")
        if self._permissions is None:
            raise IntegrationPermissionError(
                "Integration permissions are unavailable; request denied"
            )
        snapshot = self._permissions(owner_user_id, agent_id, endpoint_id)
        if snapshot is None:
            raise IntegrationPermissionError(
                "No integration permission grants this action (default deny)"
            )
        allowed = snapshot.can_call if action == "call" else snapshot.can_list
        if not allowed:
            raise IntegrationPermissionError(
                f"Integration permission does not allow action '{action}'"
            )
        decision = self._rate_limiter.try_acquire(
            f"{owner_user_id}:{agent_id}:{endpoint_id}",
            max_calls=snapshot.rate_limit_calls,
            window_seconds=snapshot.rate_limit_window_seconds,
        )
        if not decision.allowed:
            raise IntegrationRateLimitError(decision.retry_after_seconds)
        return snapshot
