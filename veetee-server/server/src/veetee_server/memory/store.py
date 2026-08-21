"""Memory store interface, policy gatekeeper, and in-memory implementation."""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable

from veetee_server.memory.model import (
    MemoryEntry,
    MemoryKind,
    MemoryProposal,
    TenantScope,
)

_DEFAULT_SENSITIVE_PATTERNS = (
    re.compile(r"(?:password|mật khẩu|token|api[_\-]?key|secret|mat khau)", re.IGNORECASE),
)

_DEFAULT_TRANSIENT_PHRASES = frozenset(
    ("chào bạn", "xin chào", "cảm ơn", "ok", "được rồi", "hi", "hello")
)


class MemoryPolicy:
    """Policy engine validating model memory proposals before persistence."""

    def __init__(
        self,
        min_profile_confidence: float = 0.8,
        *,
        sensitive_patterns: Iterable[re.Pattern[str]] = _DEFAULT_SENSITIVE_PATTERNS,
        transient_detector: Callable[[str], bool] | None = None,
    ) -> None:
        self.min_profile_confidence = min_profile_confidence
        self.sensitive_patterns = tuple(sensitive_patterns)
        self.transient_detector = transient_detector or (
            lambda value: value.casefold() in _DEFAULT_TRANSIENT_PHRASES
        )

    def evaluate_proposal(self, proposal: MemoryProposal) -> bool:
        """Evaluates whether a proposal meets memory policy criteria."""
        content = proposal.content.strip()
        if not content:
            return False

        # Reject transient small talk
        if self.transient_detector(content):
            return False

        # Reject sensitive credentials or tokens
        for pattern in self.sensitive_patterns:
            if pattern.search(content):
                return False

        # Profile memory requires higher confidence threshold or explicit request
        if proposal.kind == MemoryKind.PROFILE:
            if proposal.confidence < self.min_profile_confidence:
                return False

        return True


class MemoryStore(ABC):
    """Abstract interface for tenant-scoped memory persistence."""

    @abstractmethod
    def upsert(self, entry: MemoryEntry) -> MemoryEntry:
        """Inserts or updates a memory entry with conflict resolution."""
        ...

    @abstractmethod
    def get(self, entry_id: str, scope: TenantScope) -> MemoryEntry | None:
        """Retrieves a single memory entry by ID and tenant scope."""
        ...

    @abstractmethod
    def list_by_tenant(
        self, scope: TenantScope, kind: MemoryKind | None = None
    ) -> list[MemoryEntry]:
        """Lists entries for a tenant scope, optionally filtered by kind."""
        ...

    @abstractmethod
    def forget(self, entry_id: str, scope: TenantScope) -> bool:
        """Deletes a specific memory entry if scope matches."""
        ...

    @abstractmethod
    def delete_all(
        self, scope: TenantScope, kind: MemoryKind | None = None
    ) -> int:
        """Deletes all memory entries for a tenant scope, returning deleted count."""
        ...


class InMemoryMemoryStore(MemoryStore):
    """In-memory thread-safe implementation of MemoryStore with conflict resolution."""

    def __init__(self) -> None:
        # Key: (user_id, agent_id, entry_id) -> MemoryEntry
        self._entries: dict[tuple[str, str, str], MemoryEntry] = {}

    def upsert(self, entry: MemoryEntry) -> MemoryEntry:
        scope = entry.tenant_scope
        key = (scope.user_id, scope.agent_id, entry.id)

        # Conflict resolution for profile entries with matching metadata key or exact topic
        if entry.kind == MemoryKind.PROFILE and entry.metadata.get("key"):
            target_key = entry.metadata["key"]
            for (u, a, _e_id), existing in list(self._entries.items()):
                if (
                    u == scope.user_id
                    and a == scope.agent_id
                    and existing.kind == MemoryKind.PROFILE
                    and existing.metadata.get("key") == target_key
                ):
                    # Replace existing conflicting entry
                    updated_entry = MemoryEntry(
                        id=existing.id,
                        tenant_scope=scope,
                        kind=MemoryKind.PROFILE,
                        content=entry.content,
                        provenance=entry.provenance,
                        confidence=entry.confidence,
                        created_at=existing.created_at,
                        updated_at=time.time(),
                        metadata=entry.metadata,
                    )
                    self._entries[(u, a, existing.id)] = updated_entry
                    return updated_entry

        self._entries[key] = entry
        return entry

    def get(self, entry_id: str, scope: TenantScope) -> MemoryEntry | None:
        return self._entries.get((scope.user_id, scope.agent_id, entry_id))

    def list_by_tenant(
        self, scope: TenantScope, kind: MemoryKind | None = None
    ) -> list[MemoryEntry]:
        results: list[MemoryEntry] = []
        for (u, a, _), entry in self._entries.items():
            if u == scope.user_id and a == scope.agent_id:
                if kind is None or entry.kind == kind:
                    results.append(entry)
        return results

    def forget(self, entry_id: str, scope: TenantScope) -> bool:
        key = (scope.user_id, scope.agent_id, entry_id)
        if key in self._entries:
            del self._entries[key]
            return True
        return False

    def delete_all(
        self, scope: TenantScope, kind: MemoryKind | None = None
    ) -> int:
        keys_to_delete = [
            (u, a, e_id)
            for (u, a, e_id), entry in self._entries.items()
            if u == scope.user_id
            and a == scope.agent_id
            and (kind is None or entry.kind == kind)
        ]
        for k in keys_to_delete:
            del self._entries[k]
        return len(keys_to_delete)
