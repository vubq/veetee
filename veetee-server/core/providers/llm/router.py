"""Quota-aware route selection across independent Groq quota groups.

The router never classifies intent and never touches semantic content. It
only answers one question per inference request: which credential may send
this many tokens right now, and which one is expected to answer fastest.

- Eligibility: enabled target, group not cooling down, atomic reservation
  fits, low-priority purposes leave headroom for voice.
- Selection: lowest predicted usable-event latency (EWMA per group plus an
  in-flight penalty), with round-robin fairness jitter avoided on purpose:
  deterministic order keeps simulations reproducible.
- Admission: bounded wait, never past the caller's deadline.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.providers.llm.quota import QuotaLedger

LOW_PRIORITY_PURPOSES = frozenset({"prewarm", "benchmark", "background"})


@dataclass
class RouteTarget:
    alias: str
    api_key: str
    quota_group: str
    base_url: str
    enabled: bool = True


@dataclass
class Lease:
    alias: str
    quota_group: str
    api_key: str
    base_url: str
    reservation_id: int
    estimated_tokens: int


@dataclass
class _GroupStats:
    ewma_latency_s: float = 1.0
    samples: int = 0


class QuotaExhausted(Exception):
    """No eligible quota group could serve the request in time."""


class GroqRouter:
    def __init__(
        self,
        targets: List[RouteTarget],
        ledger: QuotaLedger,
        *,
        clock: Callable[[], float] | None = None,
        headroom_pct: float = 10.0,
        admission_wait_ms: float = 50.0,
        inflight_penalty_s: float = 0.4,
        ewma_alpha: float = 0.3,
        low_priority_purposes: frozenset = LOW_PRIORITY_PURPOSES,
    ) -> None:
        self._targets = list(targets)
        self._ledger = ledger
        self._now = clock or time.monotonic
        self._headroom = max(0.0, float(headroom_pct)) / 100.0
        self._admission_wait_s = max(0.0, float(admission_wait_ms) / 1000.0)
        self._penalty = max(0.0, float(inflight_penalty_s))
        self._alpha = min(1.0, max(0.0, float(ewma_alpha)))
        self._low_priority = frozenset(low_priority_purposes)
        self._stats: Dict[str, _GroupStats] = {}

    def disable_alias(self, alias: str, reason: str = "") -> None:
        for target in self._targets:
            if target.alias == alias:
                target.enabled = False

    def note_latency(self, group: str, latency_s: float) -> None:
        stats = self._stats.setdefault(group, _GroupStats())
        stats.samples += 1
        stats.ewma_latency_s = (
            self._alpha * max(0.0, float(latency_s))
            + (1.0 - self._alpha) * stats.ewma_latency_s
        )

    def _predicted(self, group: str, in_flight: int) -> float:
        stats = self._stats.get(group)
        base = stats.ewma_latency_s if stats else 1.0
        return base + in_flight * self._penalty

    async def _try_once(
        self,
        estimated_tokens: int,
        purpose: str,
        exclude_groups: frozenset,
    ) -> Optional[Lease]:
        now = self._now()
        candidates: list[tuple[float, RouteTarget, int]] = []
        for target in self._targets:
            if not target.enabled or not target.api_key:
                continue
            if target.quota_group in exclude_groups:
                continue
            snap = await self._ledger.snapshot(target.quota_group)
            if not snap.get("known"):
                in_flight = 0
            else:
                if now < float(snap.get("cooldown_until") or 0.0):
                    continue
                in_flight = int(snap.get("in_flight") or 0)
                if purpose in self._low_priority and self._headroom > 0:
                    remaining = (snap.get("remaining") or {}).get("tpm")
                    limit = (snap.get("limits") or {}).get("tpm")
                    if (isinstance(remaining, (int, float))
                            and isinstance(limit, (int, float)) and limit > 0
                            and remaining < limit * self._headroom + estimated_tokens):
                        continue
            candidates.append(
                (self._predicted(target.quota_group, in_flight), target, in_flight))
        candidates.sort(key=lambda item: (item[0], item[1].alias))
        for _, target, _ in candidates:
            reservation_id = await self._ledger.try_reserve(
                target.quota_group, tokens=float(estimated_tokens))
            if reservation_id is not None:
                return Lease(
                    alias=target.alias,
                    quota_group=target.quota_group,
                    api_key=target.api_key,
                    base_url=target.base_url,
                    reservation_id=reservation_id,
                    estimated_tokens=estimated_tokens,
                )
        return None

    async def acquire(
        self,
        estimated_tokens: int,
        *,
        purpose: str = "chat",
        deadline: Optional[float] = None,
        exclude_groups: frozenset = frozenset(),
    ) -> Lease:
        """Reserve quota for one inference request or raise QuotaExhausted."""
        estimated_tokens = max(1, int(estimated_tokens))
        lease = await self._try_once(estimated_tokens, purpose, exclude_groups)
        if lease is not None:
            return lease
        if self._admission_wait_s > 0:
            budget = self._admission_wait_s
            if deadline is not None:
                budget = min(budget, max(0.0, deadline - self._now()))
            if budget > 0:
                await asyncio.sleep(min(budget, self._admission_wait_s))
                lease = await self._try_once(estimated_tokens, purpose, exclude_groups)
                if lease is not None:
                    return lease
        raise QuotaExhausted(
            f"no eligible quota group for ~{estimated_tokens} tokens "
            f"(purpose={purpose})"
        )

    async def settle_ok(self, lease: Lease, actual_tokens: int = 0) -> None:
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id,
            outcome="ok", actual_tokens=max(0, int(actual_tokens)))

    async def settle_rejected(self, lease: Lease) -> None:
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id, outcome="rejected")

    async def settle_uncertain(self, lease: Lease) -> None:
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id, outcome="uncertain")

    async def cooldown(self, group: str, retry_after_s: float, reason: str = "") -> None:
        await self._ledger.set_cooldown(
            group, self._now() + max(0.0, float(retry_after_s)), reason)

    def aliases(self) -> List[Dict[str, Any]]:
        """Sanitized inventory for logs/diagnostics (never keys)."""
        return [
            {"alias": t.alias, "quota_group": t.quota_group, "enabled": t.enabled}
            for t in self._targets
        ]
