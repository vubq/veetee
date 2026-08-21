"""Hybrid memory retrieval with tenant isolation and configurable scoring."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any

from veetee_server.memory.model import MemoryEntry, MemoryKind, TenantScope
from veetee_server.memory.store import MemoryStore


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """Retrieval query params with strict tenant scope."""

    scope: TenantScope
    query_text: str = ""
    kinds: list[MemoryKind] | None = None
    limit: int = 5
    min_confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class MemoryRetrieverConfig:
    """Configurable scoring weights for hybrid retrieval."""

    weight_semantic: float = 0.5
    weight_recency: float = 0.3
    weight_confidence: float = 0.2
    recency_half_life_hours: float = 24.0


def _tokenize(text: str) -> set[str]:
    """Basic lowercased tokenization for semantic overlap calculation."""
    words = re.findall(r"\w+", text.lower())
    return set(words)


def calculate_semantic_similarity(query: str, content: str) -> float:
    """Calculates Jaccard token similarity between query and memory content."""
    if not query or not content:
        return 0.5  # Neutral default score when query is empty
    q_tokens = _tokenize(query)
    c_tokens = _tokenize(content)
    if not q_tokens or not c_tokens:
        return 0.0
    intersection = q_tokens.intersection(c_tokens)
    union = q_tokens.union(c_tokens)
    return len(intersection) / len(union)


def calculate_recency_score(created_at: float, now: float, half_life_hours: float) -> float:
    """Exponential decay score based on age in hours."""
    age_seconds = max(0.0, now - created_at)
    half_life_seconds = half_life_hours * 3600.0
    if half_life_seconds <= 0:
        return 1.0
    return math.exp(-math.log(2.0) * (age_seconds / half_life_seconds))


class MemoryRetriever:
    """Retriever searching memory store under tenant isolation and multi-factor ranking."""

    def __init__(
        self,
        store: MemoryStore,
        config: MemoryRetrieverConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or MemoryRetrieverConfig()

    def retrieve(self, query: RetrievalQuery) -> list[MemoryEntry]:
        """Retrieves and ranks entries matching query within strict tenant scope."""
        # 1. Fetch entries matching tenant scope
        entries: list[MemoryEntry] = []
        if query.kinds:
            for k in query.kinds:
                entries.extend(self.store.list_by_tenant(query.scope, kind=k))
        else:
            entries = self.store.list_by_tenant(query.scope)

        # Filter out entries below min confidence
        entries = [e for e in entries if e.confidence >= query.min_confidence]

        if not entries:
            return []

        now = time.time()
        scored_entries: list[tuple[float, MemoryEntry]] = []

        for entry in entries:
            sem_score = calculate_semantic_similarity(query.query_text, entry.content)
            rec_score = calculate_recency_score(
                entry.created_at, now, self.config.recency_half_life_hours
            )
            conf_score = entry.confidence

            total_score = (
                self.config.weight_semantic * sem_score
                + self.config.weight_recency * rec_score
                + self.config.weight_confidence * conf_score
            )
            scored_entries.append((total_score, entry))

        # Sort descending by composite score
        scored_entries.sort(key=lambda item: item[0], reverse=True)

        return [entry for _, entry in scored_entries[: query.limit]]

    def format_memories_for_prompt(self, entries: list[MemoryEntry]) -> list[dict[str, Any]]:
        """Formats entries into safe untrusted data structures for ContextAssembler."""
        formatted: list[dict[str, Any]] = []
        for entry in entries:
            formatted.append(
                {
                    "id": entry.id,
                    "kind": entry.kind.value,
                    "provenance": entry.provenance,
                    "confidence": entry.confidence,
                    "content": entry.content,
                }
            )
        return formatted
