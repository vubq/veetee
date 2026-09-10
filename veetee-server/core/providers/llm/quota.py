"""Atomic quota ledger for LLM quota groups.

A quota *group* identifies one independent rate-limit scope (for example one
Groq organization). API keys are credentials; several keys may share one
group and must then share its budget — the ledger never multiplies quota by
key count.

Dimensions are sliding windows: ``rpm``/``rpd`` for requests and
``tpm``/``tpd`` for tokens (plus optional ``itpm``/``otpm``). Unknown limits
start in discovery mode (bounded in-flight only) until response headers teach
real caps. All check-and-reserve mutations are atomic under one lock; no
network I/O ever happens inside the critical section.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional, Tuple

# Dimension -> window seconds. Unknown dimensions are rejected, never assumed.
DIMENSION_WINDOWS: Dict[str, float] = {
    "rpm": 60.0,
    "rpd": 86400.0,
    "tpm": 60.0,
    "tpd": 86400.0,
    "itpm": 60.0,
    "otpm": 60.0,
}

_REQUEST_DIMS = ("rpm", "rpd")
_TOKEN_DIMS = ("tpm", "tpd", "itpm", "otpm")


@dataclass
class _Entry:
    timestamp: float
    dimension: str
    amount: float
    reservation_id: int


@dataclass
class _GroupState:
    limits: Dict[str, float] = field(default_factory=dict)
    entries: Deque[_Entry] = field(default_factory=deque)
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    observed_remaining: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    in_flight: int = 0
    discovery: bool = True


class QuotaLedger:
    """Thread-safe (single-loop) quota accounting with atomic reservations."""

    def __init__(
        self,
        groups: Optional[Dict[str, Dict[str, float]]] = None,
        *,
        clock: Callable[[], float] | None = None,
        discovery_max_inflight: int = 1,
    ) -> None:
        self._now = clock or time.monotonic
        self._discovery_max_inflight = max(1, int(discovery_max_inflight))
        self._lock = asyncio.Lock()
        self._states: Dict[str, _GroupState] = {}
        self._id_counter = itertools.count(1)
        for name, limits in (groups or {}).items():
            self.configure_group(name, limits)

    def configure_group(self, name: str, limits: Dict[str, float]) -> None:
        """Set (or replace) known caps for a group. Unknown dims raise."""
        clean: Dict[str, float] = {}
        for dim, value in (limits or {}).items():
            if dim not in DIMENSION_WINDOWS:
                raise ValueError(f"unknown quota dimension: {dim}")
            number = float(value)
            if number <= 0:
                raise ValueError(f"quota limit must be positive: {dim}={value}")
            clean[dim] = number
        state = self._states.setdefault(name, _GroupState())
        state.limits = clean
        state.discovery = not clean

    def _prune(self, state: _GroupState, now: float) -> None:
        while state.entries and now - state.entries[0].timestamp >= DIMENSION_WINDOWS.get(
            state.entries[0].dimension, 60.0
        ):
            state.entries.popleft()
        # Bounded retention even under clock skew.
        while len(state.entries) > 100000:
            state.entries.popleft()

    def _spent(self, state: _GroupState, dim: str) -> float:
        window = DIMENSION_WINDOWS[dim]
        now = self._now()
        return sum(
            entry.amount
            for entry in state.entries
            if entry.dimension == dim and now - entry.timestamp < window
        )

    def _effective_remaining(self, state: _GroupState, dim: str, now: float) -> Optional[float]:
        limit = state.limits.get(dim)
        if limit is None:
            return None
        remaining = limit - self._spent(state, dim)
        observed = state.observed_remaining.get(dim)
        if observed is not None:
            value, seen_at = observed
            if now - seen_at < DIMENSION_WINDOWS[dim]:
                remaining = min(remaining, value)
        return remaining

    async def try_reserve(
        self, group: str, *, requests: int = 1, tokens: float = 0
    ) -> Optional[int]:
        """Atomically check and reserve budget. Returns id or None."""
        async with self._lock:
            now = self._now()
            state = self._states.setdefault(group, _GroupState())
            self._prune(state, now)
            if now < state.cooldown_until:
                return None
            if state.discovery:
                if state.in_flight >= self._discovery_max_inflight:
                    return None
            else:
                need = {"requests": float(requests), "tokens": float(tokens)}
                for dim in _REQUEST_DIMS:
                    limit = state.limits.get(dim)
                    if limit is not None:
                        remaining = self._effective_remaining(state, dim, now)
                        if remaining is not None and remaining < need["requests"]:
                            return None
                for dim in _TOKEN_DIMS:
                    limit = state.limits.get(dim)
                    if limit is not None:
                        remaining = self._effective_remaining(state, dim, now)
                        if remaining is not None and remaining < need["tokens"]:
                            return None
            reservation_id = next(self._id_counter)
            # Charge every configured window of each kind, so rpm AND rpd
            # (or tpm AND tpd) both observe the spend.
            for dim in _REQUEST_DIMS:
                if dim in state.limits:
                    state.entries.append(
                        _Entry(now, dim, float(requests), reservation_id))
            if tokens:
                for dim in _TOKEN_DIMS:
                    if dim in state.limits:
                        state.entries.append(
                            _Entry(now, dim, float(tokens), reservation_id))
            state.in_flight += 1
            return reservation_id

    async def settle(
        self,
        group: str,
        reservation_id: int,
        *,
        outcome: str,
        actual_tokens: float = 0,
    ) -> None:
        """Settle a reservation: ok | rejected | uncertain.

        - ok: reconcile the token charge down to actual usage when known.
        - rejected (e.g. HTTP 429 before dispatch): remove the charge fully.
        - uncertain (timeout/cancel after dispatch): keep the estimate, since
          the upstream may still have processed and billed the request.
        """
        if outcome not in ("ok", "rejected", "uncertain"):
            raise ValueError(f"unknown settle outcome: {outcome}")
        async with self._lock:
            state = self._states.get(group)
            if state is None:
                return
            now = self._now()
            if outcome == "rejected":
                state.entries = deque(
                    entry for entry in state.entries
                    if entry.reservation_id != reservation_id
                )
            elif outcome == "ok" and actual_tokens >= 0:
                kept: Deque[_Entry] = deque()
                for entry in state.entries:
                    if entry.reservation_id == reservation_id and entry.dimension in _TOKEN_DIMS:
                        if actual_tokens:
                            kept.append(_Entry(
                                now, entry.dimension, float(actual_tokens),
                                reservation_id))
                        # else: drop the estimated charge entirely.
                    else:
                        kept.append(entry)
                state.entries = kept
            # uncertain: keep everything as charged.
            state.in_flight = max(0, state.in_flight - 1)
            self._prune(state, now)

    async def set_cooldown(self, group: str, until: float, reason: str = "") -> None:
        async with self._lock:
            state = self._states.setdefault(group, _GroupState())
            if until > state.cooldown_until:
                state.cooldown_until = until
                state.cooldown_reason = reason

    async def note_limits(self, group: str, limits: Dict[str, float]) -> None:
        """Learn real caps from response headers (discovery -> configured)."""
        async with self._lock:
            state = self._states.setdefault(group, _GroupState())
            for dim, value in (limits or {}).items():
                if dim in DIMENSION_WINDOWS and float(value) > 0:
                    state.limits[dim] = float(value)
            if state.limits:
                state.discovery = False

    async def note_remaining(self, group: str, remaining: Dict[str, float]) -> None:
        """Tighten effective budget with provider-observed remaining values."""
        async with self._lock:
            now = self._now()
            state = self._states.setdefault(group, _GroupState())
            for dim, value in (remaining or {}).items():
                if dim in DIMENSION_WINDOWS and float(value) >= 0:
                    state.observed_remaining[dim] = (float(value), now)

    async def snapshot(self, group: str) -> Dict[str, object]:
        """Sanitized state for telemetry (no keys, no prompts)."""
        async with self._lock:
            now = self._now()
            state = self._states.get(group)
            if state is None:
                return {"group": group, "known": False}
            self._prune(state, now)
            remaining = {
                dim: self._effective_remaining(state, dim, now)
                for dim in DIMENSION_WINDOWS
                if dim in state.limits
            }
            return {
                "group": group,
                "known": True,
                "limits": dict(state.limits),
                "remaining": remaining,
                "in_flight": state.in_flight,
                "discovery": state.discovery,
                "cooldown_until": state.cooldown_until,
                "cooldown_reason": state.cooldown_reason,
            }
