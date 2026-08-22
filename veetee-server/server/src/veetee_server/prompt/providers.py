"""Context Provider Registry, typed adapters, per-provider timeout, and tenant-aware caching."""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from veetee_server.memory.model import TenantScope
from veetee_server.memory.store import MemoryStore
from veetee_server.persistence.correction import (
    ContextProviderConfigRepository,
    StoredContextProviderConfig,
)
from veetee_server.persistence.knowledge import KnowledgeRepository
from veetee_server.untrusted import sanitize_untrusted_text

# Upper bound for cached provider results; entries also expire by TTL.
_CACHE_MAX_ENTRIES = 256


@dataclass(frozen=True, slots=True)
class ProviderContextResult:
    provider_type: Literal["runtime", "memory", "knowledge_fts", "weather"]
    status: Literal["ok", "timeout", "error", "unavailable"]
    content: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: float = 0.0


class BaseContextProvider(ABC):
    provider_type: Literal["runtime", "memory", "knowledge_fts", "weather"]

    @abstractmethod
    async def fetch(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        timeout_ms: int,
        config: dict[str, Any],
    ) -> ProviderContextResult:
        """Fetches context snippet from provider."""
        ...


class RuntimeContextProvider(BaseContextProvider):
    provider_type = "runtime"

    async def fetch(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        timeout_ms: int,
        config: dict[str, Any],
    ) -> ProviderContextResult:
        start_time = time.monotonic()
        now_iso = datetime.now(UTC).isoformat()
        content = f"Current UTC Time: {now_iso}\nAgent ID: {agent_id}"
        elapsed = (time.monotonic() - start_time) * 1000
        return ProviderContextResult(
            provider_type="runtime",
            status="ok",
            content=content,
            citations=[],
            provenance={"timestamp": now_iso, "agent_id": str(agent_id)},
            execution_time_ms=elapsed,
        )


class MemoryContextProvider(BaseContextProvider):
    provider_type = "memory"

    def __init__(self, memory_store: MemoryStore | None = None) -> None:
        self.memory_store = memory_store

    async def fetch(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        timeout_ms: int,
        config: dict[str, Any],
    ) -> ProviderContextResult:
        start_time = time.monotonic()
        if not self.memory_store:
            return ProviderContextResult(
                provider_type="memory",
                status="ok",
                content="",
                citations=[],
                provenance={"reason": "memory_store_not_configured"},
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        memories = self.memory_store.list_by_tenant(
            TenantScope(user_id=str(owner_user_id), agent_id=str(agent_id))
        )
        blocks: list[str] = []
        citations: list[dict[str, Any]] = []

        query_terms = query.casefold().split()
        matched = [
            memory
            for memory in memories
            if not query_terms
            or any(term in memory.content.casefold() for term in query_terms)
        ]
        for memory in matched:
            sanitized = sanitize_untrusted_text(memory.content)
            mem_id = memory.id
            blocks.append(f"- [Memory {mem_id}]: {sanitized}")
            citations.append({"type": "memory", "id": mem_id})

        content = "\n".join(blocks)
        elapsed = (time.monotonic() - start_time) * 1000
        return ProviderContextResult(
            provider_type="memory",
            status="ok",
            content=content,
            citations=citations,
            provenance={"memory_count": len(matched)},
            execution_time_ms=elapsed,
        )


class KnowledgeFTSContextProvider(BaseContextProvider):
    provider_type = "knowledge_fts"

    def __init__(self, knowledge_repository: KnowledgeRepository | None = None) -> None:
        self.knowledge_repository = knowledge_repository

    async def fetch(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        timeout_ms: int,
        config: dict[str, Any],
    ) -> ProviderContextResult:
        start_time = time.monotonic()
        if not self.knowledge_repository or not query.strip():
            return ProviderContextResult(
                provider_type="knowledge_fts",
                status="ok",
                content="",
                citations=[],
                provenance={"reason": "no_repository_or_query"},
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # Get datasets attached to agent
        datasets = await asyncio.to_thread(
            self.knowledge_repository.list_agent_datasets, owner_user_id, agent_id
        )
        if not datasets:
            return ProviderContextResult(
                provider_type="knowledge_fts",
                status="ok",
                content="",
                citations=[],
                provenance={"reason": "no_datasets_linked"},
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        dataset_ids = [d.id for d in datasets if d.status == "active"]
        limit = int(config.get("limit", 5))
        max_chars = int(config.get("max_chars", 2000))

        search_results = await asyncio.to_thread(
            self.knowledge_repository.search_chunks,
            owner_user_id,
            dataset_ids,
            query,
            limit,
            max_chars,
        )

        blocks: list[str] = []
        citations: list[dict[str, Any]] = []

        for r in search_results:
            clean_c = sanitize_untrusted_text(r.content)
            blocks.append(
                f'<untrusted_knowledge chunk_id="{r.chunk_id}" doc_id="{r.document_id}" '
                f'filename="{r.filename}" range="{r.char_start}-{r.char_end}">\n'
                f"{clean_c}\n"
                f"</untrusted_knowledge>"
            )
            citations.append(
                {
                    "type": "knowledge",
                    "chunk_id": str(r.chunk_id),
                    "document_id": str(r.document_id),
                    "dataset_id": str(r.dataset_id),
                    "filename": r.filename,
                    "char_start": r.char_start,
                    "char_end": r.char_end,
                    "score": r.score,
                }
            )

        content = "\n".join(blocks)
        elapsed = (time.monotonic() - start_time) * 1000
        return ProviderContextResult(
            provider_type="knowledge_fts",
            status="ok",
            content=content,
            citations=citations,
            provenance={"matched_chunks": len(search_results), "dataset_count": len(dataset_ids)},
            execution_time_ms=elapsed,
        )


class WeatherContextProvider(BaseContextProvider):
    """M6.6 placeholder integration. Reports unavailable status."""

    provider_type = "weather"

    async def fetch(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        timeout_ms: int,
        config: dict[str, Any],
    ) -> ProviderContextResult:
        start_time = time.monotonic()
        return ProviderContextResult(
            provider_type="weather",
            status="unavailable",
            content="",
            citations=[],
            provenance={"reason": "Weather context provider integration is planned for M6.6"},
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )


@dataclass(slots=True)
class _CacheEntry:
    result: ProviderContextResult
    expires_at: float


class ContextProviderRegistry:
    """Registry managing context providers, timeout limits, and tenant/query TTL cache."""

    def __init__(
        self,
        config_repository: ContextProviderConfigRepository | None = None,
        knowledge_repository: KnowledgeRepository | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.config_repository = config_repository
        self.providers: dict[str, BaseContextProvider] = {
            "runtime": RuntimeContextProvider(),
            "memory": MemoryContextProvider(memory_store),
            "knowledge_fts": KnowledgeFTSContextProvider(knowledge_repository),
            "weather": WeatherContextProvider(),
        }
        self._cache: dict[str, _CacheEntry] = {}

    def register_provider(self, provider: BaseContextProvider) -> None:
        self.providers[provider.provider_type] = provider

    async def fetch_all(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        query: str,
        default_timeout_ms: int = 2000,
    ) -> list[ProviderContextResult]:
        """Fetches context from all enabled providers for an agent in ordinal order.

        Individual provider timeouts/errors degrade softly without breaking other providers.
        """
        configs: list[StoredContextProviderConfig] = []
        if self.config_repository:
            configs = await asyncio.to_thread(
                self.config_repository.list_agent_configs, owner_user_id, agent_id
            )

        if not configs:
            # Default fallback: enable runtime and knowledge_fts
            enabled_types: list[tuple[str, int, int, int, dict[str, Any]]] = [
                ("runtime", default_timeout_ms, 0, 0, {}),
                ("knowledge_fts", default_timeout_ms, 0, 0, {}),
            ]
        else:
            enabled_types = [
                (c.provider_type, c.timeout_ms, c.cache_ttl_seconds, c.version, c.config)
                for c in sorted(configs, key=lambda x: x.ordinal)
                if c.enabled
            ]

        results: list[ProviderContextResult] = []
        now = time.monotonic()

        for p_type, timeout_ms, ttl_seconds, config_version, p_config in enabled_types:
            provider = self.providers.get(p_type)
            if not provider:
                results.append(
                    ProviderContextResult(
                        provider_type=p_type,  # type: ignore
                        status="unavailable",
                        content="",
                        provenance={"reason": "provider_not_registered"},
                    )
                )
                continue

            # Cache lookup
            cache_key = (
                f"{owner_user_id}:{agent_id}:{p_type}:{config_version}:{query}:{p_config}"
            )
            if ttl_seconds > 0:
                cached = self._cache.get(cache_key)
                if cached and cached.expires_at > now:
                    results.append(cached.result)
                    continue

            # Execute provider with timeout
            try:
                result = await asyncio.wait_for(
                    provider.fetch(owner_user_id, agent_id, query, timeout_ms, p_config),
                    timeout=timeout_ms / 1000.0,
                )
            except TimeoutError:
                result = ProviderContextResult(
                    provider_type=p_type,  # type: ignore
                    status="timeout",
                    content="",
                    citations=[],
                    provenance={"error": f"Provider {p_type} timed out after {timeout_ms}ms"},
                    execution_time_ms=float(timeout_ms),
                )
            except Exception as exc:
                result = ProviderContextResult(
                    provider_type=p_type,  # type: ignore
                    status="error",
                    content="",
                    citations=[],
                    provenance={"error": str(exc)},
                    execution_time_ms=0.0,
                )

            if ttl_seconds > 0 and result.status == "ok":
                self._prune_expired(now)
                self._cache[cache_key] = _CacheEntry(result=result, expires_at=now + ttl_seconds)

            results.append(result)

        return results

    def _prune_expired(self, now: float) -> None:
        """Drops expired entries so the per-tenant cache cannot grow unbounded."""
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            del self._cache[key]
        if len(self._cache) > _CACHE_MAX_ENTRIES:
            # Keep the most recently expiring entries within the bounded size.
            keep = sorted(
                self._cache.items(), key=lambda item: item[1].expires_at, reverse=True
            )[:_CACHE_MAX_ENTRIES]
            self._cache = dict(keep)
