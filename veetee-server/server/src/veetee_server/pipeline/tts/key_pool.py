"""Gemini key pool with bounded cooldown and one half-open probe per key."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .errors import TTSKeyExhaustedError

logger = logging.getLogger("veetee.tts.key_pool")


class KeyPoolState(StrEnum):
    HEALTHY = "healthy"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass(slots=True)
class KeyEntry:
    key_id: str
    secret_key: str
    in_flight: int = 0
    state: KeyPoolState = KeyPoolState.HEALTHY
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_used: float = 0.0
    half_open_probe: bool = False

    def __repr__(self) -> str:
        return (
            f"KeyEntry(key_id={self.key_id!r}, in_flight={self.in_flight}, "
            f"state={self.state.value!r})"
        )


class GeminiKeyPool:
    """Selects the least-loaded healthy key with fair tie breaking."""

    def __init__(
        self,
        api_keys: list[str],
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._time = time_func
        self._entries = [
            KeyEntry(
                key_id=f"key_{index}_{hashlib.sha256(key.encode()).hexdigest()[:8]}",
                secret_key=key,
            )
            for index, raw in enumerate(api_keys)
            if (key := raw.strip())
        ]
        self._rr_index = 0

    @property
    def total_keys(self) -> int:
        return len(self._entries)

    @property
    def healthy_keys_count(self) -> int:
        now = self._time()
        return sum(self._available(entry, now) for entry in self._entries)

    def acquire_key(self, excluded: set[str] | None = None) -> KeyEntry:
        now = self._time()
        excluded = excluded or set()
        available = [
            entry
            for entry in self._entries
            if entry.key_id not in excluded and self._available(entry, now)
        ]
        if not available:
            raise TTSKeyExhaustedError("All Gemini API keys are unavailable")
        minimum = min(entry.in_flight for entry in available)
        tied = [entry for entry in available if entry.in_flight == minimum]
        selected = tied[self._rr_index % len(tied)]
        self._rr_index += 1
        if selected.state is KeyPoolState.COOLDOWN:
            selected.half_open_probe = True
        selected.in_flight += 1
        selected.last_used = now
        return selected

    def release_key(self, key_id: str) -> None:
        entry = self._get(key_id)
        entry.in_flight = max(0, entry.in_flight - 1)

    def record_auth_failure(self, key_id: str) -> None:
        entry = self._get(key_id)
        entry.state = KeyPoolState.DISABLED
        entry.half_open_probe = False
        logger.warning("gemini_key_disabled", extra={"key_id": key_id})

    def record_rate_limit(self, key_id: str, cooldown_seconds: float) -> None:
        entry = self._get(key_id)
        self._cooldown(entry, cooldown_seconds)
        logger.warning(
            "gemini_key_rate_limited",
            extra={"key_id": key_id, "cooldown_seconds": cooldown_seconds},
        )

    def record_transient_failure(
        self,
        key_id: str,
        failure_threshold: int,
        cooldown_seconds: float,
    ) -> None:
        entry = self._get(key_id)
        entry.consecutive_failures += 1
        if entry.half_open_probe or entry.consecutive_failures >= failure_threshold:
            self._cooldown(entry, cooldown_seconds)

    def record_success(self, key_id: str) -> None:
        entry = self._get(key_id)
        entry.consecutive_failures = 0
        entry.cooldown_until = 0.0
        entry.half_open_probe = False
        entry.state = KeyPoolState.HEALTHY

    def _available(self, entry: KeyEntry, now: float) -> bool:
        if entry.state is KeyPoolState.DISABLED:
            return False
        if entry.state is KeyPoolState.COOLDOWN:
            return now >= entry.cooldown_until and not entry.half_open_probe
        return True

    def _cooldown(self, entry: KeyEntry, seconds: float) -> None:
        entry.state = KeyPoolState.COOLDOWN
        entry.cooldown_until = self._time() + seconds
        entry.half_open_probe = False

    def _get(self, key_id: str) -> KeyEntry:
        for entry in self._entries:
            if entry.key_id == key_id:
                return entry
        raise KeyError(key_id)
