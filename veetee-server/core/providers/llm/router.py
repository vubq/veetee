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
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.providers.llm.quota import QuotaLedger

logger = logging.getLogger("GroqRouter")

LOW_PRIORITY_PURPOSES = frozenset({"prewarm", "benchmark", "background"})


@dataclass
class RouteTarget:
    alias: str
    api_key: str
    quota_group: str
    base_url: str
    enabled: bool = True
    env_key: str = ""
    credential_id: str = ""


@dataclass
class Lease:
    alias: str
    quota_group: str
    api_key: str
    base_url: str
    reservation_id: int
    estimated_tokens: int
    env_key: str = ""
    credential_id: str = ""
    token_budget_reservation_id: Optional[int] = None


@dataclass
class _GroupStats:
    ewma_latency_s: float = 0.0
    ewma_abs_deviation_s: float = 0.0
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
        discovery_wait_ms: float = 750.0,
        inflight_penalty_s: float = 0.4,
        ewma_alpha: float = 0.3,
        jitter_penalty: float = 0.75,
        low_priority_purposes: frozenset = LOW_PRIORITY_PURPOSES,
        usage_store: Any = None,
    ) -> None:
        self._targets = list(targets)
        self._ledger = ledger
        self._now = clock or time.monotonic
        self._headroom = max(0.0, float(headroom_pct)) / 100.0
        self._admission_wait_s = max(0.0, float(admission_wait_ms) / 1000.0)
        self._discovery_wait_s = max(0.0, float(discovery_wait_ms) / 1000.0)
        self._penalty = max(0.0, float(inflight_penalty_s))
        self._alpha = min(1.0, max(0.0, float(ewma_alpha)))
        self._jitter_penalty = max(0.0, float(jitter_penalty))
        self._low_priority = frozenset(low_priority_purposes)
        self._stats: Dict[str, _GroupStats] = {}
        # Settled reservation ids: first settle wins, later ones no-op.
        # Abandoned generators, task cancels and explicit settles all funnel
        # here, so no path can double-charge or double-release a slot.
        self._settled: Dict[int, float] = {}
        self._usage_flush_tasks: set[asyncio.Task] = set()
        self._usage_store = None
        if usage_store is not None:
            self.bind_usage_store(usage_store)

    def bind_usage_store(self, usage_store: Any) -> None:
        """Bind persistent per-credential daily token accounting.

        This is intentionally independent of provider RPM/TPM quota windows:
        the user-defined limit belongs to one exact credential instance and is
        reset when that credential is deleted/replaced.
        """
        self._usage_store = usage_store
        if usage_store is None:
            for target in self._targets:
                target.credential_id = ""
            return
        for target in self._targets:
            if not target.env_key or not target.api_key:
                continue
            row = usage_store.ensure_groq_credential(
                target.env_key, target.api_key
            )
            target.credential_id = str(row.get("instance_id") or "")

    def _budget_snapshot(self, target: RouteTarget) -> Optional[Dict[str, Any]]:
        store = self._usage_store
        if (
            store is None
            or not target.env_key
            or not target.credential_id
        ):
            return None
        return store.groq_budget_snapshot(
            target.env_key, target.credential_id
        )

    def _schedule_usage_flush(self) -> None:
        store = self._usage_store
        if store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def flush_quietly() -> None:
            try:
                await asyncio.to_thread(store.flush)
            except Exception as exc:
                logger.warning("Groq token usage persistence failed: %s", exc)

        task = loop.create_task(flush_quietly())
        self._usage_flush_tasks.add(task)

        def done_callback(done: asyncio.Task) -> None:
            self._usage_flush_tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(done_callback)

    async def flush_usage(self) -> None:
        tasks = tuple(self._usage_flush_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def configure(
        self,
        *,
        headroom_pct: Optional[float] = None,
        admission_wait_ms: Optional[float] = None,
        discovery_wait_ms: Optional[float] = None,
        discovery_max_inflight: Optional[int] = None,
        inflight_penalty_s: Optional[float] = None,
        ewma_alpha: Optional[float] = None,
        jitter_penalty: Optional[float] = None,
    ) -> None:
        """Hot-apply non-semantic routing/admission policy."""
        if headroom_pct is not None:
            self._headroom = max(0.0, float(headroom_pct)) / 100.0
        if admission_wait_ms is not None:
            self._admission_wait_s = max(
                0.0, float(admission_wait_ms) / 1000.0
            )
        if discovery_wait_ms is not None:
            self._discovery_wait_s = max(
                0.0, float(discovery_wait_ms) / 1000.0
            )
        if inflight_penalty_s is not None:
            self._penalty = max(0.0, float(inflight_penalty_s))
        if ewma_alpha is not None:
            self._alpha = min(1.0, max(0.0, float(ewma_alpha)))
        if jitter_penalty is not None:
            self._jitter_penalty = max(0.0, float(jitter_penalty))
        if discovery_max_inflight is not None:
            await self._ledger.configure_discovery_max_inflight(
                int(discovery_max_inflight)
            )

    def config_snapshot(self) -> Dict[str, float]:
        return {
            "headroom_pct": round(self._headroom * 100.0, 3),
            "admission_wait_ms": round(self._admission_wait_s * 1000.0, 3),
            "discovery_wait_ms": round(self._discovery_wait_s * 1000.0, 3),
            "inflight_penalty_s": round(self._penalty, 6),
            "latency_ewma_alpha": round(self._alpha, 6),
            "latency_jitter_penalty": round(self._jitter_penalty, 6),
        }

    def disable_alias(self, alias: str, reason: str = "") -> None:
        for target in self._targets:
            if target.alias == alias:
                target.enabled = False

    def note_latency(self, group: str, latency_s: float) -> None:
        observed = max(0.0, float(latency_s))
        stats = self._stats.setdefault(group, _GroupStats())
        if stats.samples == 0:
            # Do not blend the first real measurement with an arbitrary 1s
            # prior; that made newly observed fast routes look slow for many
            # turns and prevented the router from converging quickly.
            stats.ewma_latency_s = observed
            stats.ewma_abs_deviation_s = 0.0
            stats.samples = 1
            return
        previous = stats.ewma_latency_s
        deviation = abs(observed - previous)
        stats.ewma_latency_s = (
            self._alpha * observed
            + (1.0 - self._alpha) * previous
        )
        stats.ewma_abs_deviation_s = (
            self._alpha * deviation
            + (1.0 - self._alpha) * stats.ewma_abs_deviation_s
        )
        stats.samples += 1

    def _predicted(self, group: str, in_flight: int) -> float:
        stats = self._stats.get(group)
        if stats is None or stats.samples <= 0:
            # Explore an unseen eligible group before repeatedly trusting a
            # measured route. Each group pays this cold-start bonus only until
            # it has one real first-event sample.
            base = 0.0
        else:
            base = (
                stats.ewma_latency_s
                + self._jitter_penalty * stats.ewma_abs_deviation_s
            )
        return base + in_flight * self._penalty

    def latency_snapshot(self) -> Dict[str, Dict[str, float | int]]:
        return {
            group: {
                "samples": stats.samples,
                "ewma_latency_ms": round(stats.ewma_latency_s * 1000.0, 3),
                "ewma_abs_deviation_ms": round(
                    stats.ewma_abs_deviation_s * 1000.0, 3
                ),
                "predicted_idle_ms": round(
                    (
                        stats.ewma_latency_s
                        + self._jitter_penalty * stats.ewma_abs_deviation_s
                    )
                    * 1000.0,
                    3,
                ),
            }
            for group, stats in sorted(self._stats.items())
        }

    async def quota_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Return sanitized live quota state grouped by provider scope.

        API keys are intentionally never exposed. The snapshot is diagnostics
        only; admission decisions still go through the atomic ledger.
        """
        aliases_by_group: Dict[str, list[str]] = {}
        enabled_by_group: Dict[str, bool] = {}
        for target in self._targets:
            aliases_by_group.setdefault(target.quota_group, []).append(target.alias)
            enabled_by_group[target.quota_group] = (
                enabled_by_group.get(target.quota_group, False)
                or bool(target.enabled and target.api_key)
            )

        result: Dict[str, Dict[str, Any]] = {}
        for group in sorted(aliases_by_group):
            snap = await self._ledger.snapshot(group)
            credentials = []
            for target in self._targets:
                if target.quota_group != group:
                    continue
                budget = self._budget_snapshot(target)
                if budget is None:
                    continue
                credentials.append({
                    "alias": target.alias,
                    "used_tokens": int(budget.get("used_tokens") or 0),
                    "token_limit": int(budget.get("token_limit") or 0),
                    "remaining_tokens": budget.get("remaining_tokens"),
                    "reserved_tokens": int(budget.get("reserved_tokens") or 0),
                })
            result[group] = {
                "aliases": sorted(aliases_by_group[group]),
                "enabled": enabled_by_group.get(group, False),
                "known": bool(snap.get("known")),
                "limits": dict(snap.get("limits") or {}),
                "remaining": dict(snap.get("remaining") or {}),
                "in_flight": int(snap.get("in_flight") or 0),
                "discovery": bool(snap.get("discovery", False)),
                "cooldown_until": float(snap.get("cooldown_until") or 0.0),
                "cooldown_reason": str(snap.get("cooldown_reason") or ""),
                "credentials": credentials,
            }
        return result

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
            budget = self._budget_snapshot(target)
            if budget is not None:
                token_limit = int(budget.get("token_limit") or 0)
                remaining_tokens = budget.get("remaining_tokens")
                if (
                    token_limit > 0
                    and isinstance(remaining_tokens, (int, float))
                    and remaining_tokens < estimated_tokens
                ):
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
                budget_reservation_id = None
                if (
                    self._usage_store is not None
                    and target.env_key
                    and target.credential_id
                ):
                    budget_reservation_id = (
                        self._usage_store.try_reserve_groq_tokens(
                            target.env_key,
                            target.credential_id,
                            estimated_tokens,
                        )
                    )
                    if budget_reservation_id is None:
                        await self._ledger.settle(
                            target.quota_group,
                            reservation_id,
                            outcome="rejected",
                        )
                        continue
                return Lease(
                    alias=target.alias,
                    quota_group=target.quota_group,
                    api_key=target.api_key,
                    base_url=target.base_url,
                    reservation_id=reservation_id,
                    estimated_tokens=estimated_tokens,
                    env_key=target.env_key,
                    credential_id=target.credential_id,
                    token_budget_reservation_id=budget_reservation_id,
                )
        return None

    async def _has_busy_discovery_group(
        self,
        exclude_groups: frozenset,
    ) -> bool:
        now = self._now()
        seen_groups: set[str] = set()
        for target in self._targets:
            if (
                not target.enabled
                or not target.api_key
                or target.quota_group in exclude_groups
                or target.quota_group in seen_groups
            ):
                continue
            seen_groups.add(target.quota_group)
            snap = await self._ledger.snapshot(target.quota_group)
            if (
                bool(snap.get("discovery"))
                and int(snap.get("in_flight") or 0) > 0
                and now >= float(snap.get("cooldown_until") or 0.0)
            ):
                return True
        return False

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
                step = min(0.2, budget)
                remaining_wait = budget
                while remaining_wait > 0:
                    sleep_for = min(step, remaining_wait)
                    await asyncio.sleep(sleep_for)
                    remaining_wait -= sleep_for
                    lease = await self._try_once(
                        estimated_tokens, purpose, exclude_groups
                    )
                    if lease is not None:
                        return lease

        # Bootstrap/failover race: unknown quota groups intentionally allow
        # only a bounded number of discovery requests. If another request is
        # already discovering one of those groups, a short, separately
        # configured wait lets its response headers unlock real quota instead
        # of reporting false capacity exhaustion. Known exhausted groups still
        # fail after the normal admission wait above.
        if (
            self._discovery_wait_s > 0
            and await self._has_busy_discovery_group(exclude_groups)
        ):
            budget = self._discovery_wait_s
            if deadline is not None:
                budget = min(budget, max(0.0, deadline - self._now()))
            if budget > 0:
                step = min(0.05, budget)
                remaining_wait = budget
                while remaining_wait > 0:
                    sleep_for = min(step, remaining_wait)
                    await asyncio.sleep(sleep_for)
                    remaining_wait -= sleep_for
                    lease = await self._try_once(
                        estimated_tokens, purpose, exclude_groups
                    )
                    if lease is not None:
                        return lease
                    if not await self._has_busy_discovery_group(exclude_groups):
                        break
        states = []
        for target in self._targets:
            snap = await self._ledger.snapshot(target.quota_group)
            states.append(
                f"{target.alias}/{target.quota_group}:"
                f"enabled={target.enabled} "
                f"in_flight={snap.get('in_flight')} "
                f"remaining={snap.get('remaining')} "
                f"cooldown_until={snap.get('cooldown_until')} "
                f"discovery={snap.get('discovery')}"
            )
        logger.warning(
            "Quota exhausted for ~%d tokens (purpose=%s): %s",
            estimated_tokens, purpose, " | ".join(states),
        )
        raise QuotaExhausted(
            f"no eligible quota group for ~{estimated_tokens} tokens "
            f"(purpose={purpose})"
        )

    def _claim_settle(self, lease: Lease) -> bool:
        """First settle wins. Returns True if this call owns the settle."""
        now = self._now()
        if lease.reservation_id in self._settled:
            return False
        self._settled[lease.reservation_id] = now
        if len(self._settled) > 50000:
            cutoff = now - 86400.0
            self._settled = {
                rid: ts for rid, ts in self._settled.items() if ts >= cutoff
            }
        return True

    def _settle_token_budget(
        self,
        lease: Lease,
        *,
        outcome: str,
        actual_tokens: int = 0,
    ) -> None:
        store = self._usage_store
        if store is None or lease.token_budget_reservation_id is None:
            return
        charged = store.settle_groq_token_reservation(
            lease.token_budget_reservation_id,
            outcome=outcome,
            actual_tokens=max(0, int(actual_tokens)),
        )
        if charged > 0:
            self._schedule_usage_flush()

    async def settle_ok(self, lease: Lease, actual_tokens: int = 0) -> None:
        if not self._claim_settle(lease):
            return
        actual = max(0, int(actual_tokens))
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id,
            outcome="ok", actual_tokens=actual)
        self._settle_token_budget(
            lease, outcome="ok", actual_tokens=actual
        )

    async def settle_rejected(self, lease: Lease) -> None:
        if not self._claim_settle(lease):
            return
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id, outcome="rejected")
        self._settle_token_budget(lease, outcome="rejected")

    async def settle_uncertain(self, lease: Lease) -> None:
        if not self._claim_settle(lease):
            return
        await self._ledger.settle(
            lease.quota_group, lease.reservation_id, outcome="uncertain")
        self._settle_token_budget(lease, outcome="uncertain")

    async def cooldown(self, group: str, retry_after_s: float, reason: str = "") -> None:
        await self._ledger.set_cooldown(
            group, self._now() + max(0.0, float(retry_after_s)), reason)

    def aliases(self) -> List[Dict[str, Any]]:
        """Sanitized inventory for logs/diagnostics (never keys)."""
        return [
            {"alias": t.alias, "quota_group": t.quota_group, "enabled": t.enabled}
            for t in self._targets
        ]
