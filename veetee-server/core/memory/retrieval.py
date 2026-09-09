from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Protocol

from core.memory.models import MemoryFact
from core.memory.store import MemoryStore


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = normalized.replace("đ", "d")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1}


@dataclass(frozen=True)
class RetrievalQuery:
    query: str
    owner_id: str
    scope: str
    max_results: int = 6
    deadline_ms: int = 500


@dataclass(frozen=True)
class RetrievalCandidate:
    id: str
    version: int
    source: str
    score: float
    observed_at: str
    value: str
    provenance: str = ""


class BaseRetriever(Protocol):
    async def retrieve_candidates(self, request: RetrievalQuery) -> List[RetrievalCandidate]:
        ...


EmbedFn = Callable[[str], Awaitable[List[float]]]


def _fact_to_candidate(fact: MemoryFact, *, score: float, provenance: str) -> RetrievalCandidate:
    return RetrievalCandidate(
        id=str(fact.id),
        version=int(fact.revision),
        source=f"{fact.scope}:{fact.key}",
        score=float(score),
        observed_at=str(fact.updated_at or ""),
        value=str(fact.value or ""),
        provenance=provenance,
    )


class MemoryRetriever:
    """Lexical baseline retriever with an explicit embedding seam.

    FTS/BM25/LIKE in the store is the valid technical baseline. An optional
    embedding scorer only re-ranks when it improves quality within budget;
    retrieval never fabricates facts and never crosses owner boundaries.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        top_k: int = 6,
        embed_fn: Optional[EmbedFn] = None,
        rerank_timeout_ms: int = 150,
    ):
        self.store = store
        self.top_k = max(1, int(top_k))
        self.embed_fn = embed_fn
        self.rerank_timeout_ms = max(1, int(rerank_timeout_ms))
        self.metrics: Dict[str, int] = {"hit": 0, "miss": 0, "timeout": 0, "truncated": 0}

    async def retrieve(self, *, owner_id: str, scope: str, query: str) -> List[MemoryFact]:
        request = RetrievalQuery(query=query, owner_id=owner_id, scope=scope, max_results=self.top_k)
        facts = await self.retrieve_facts(request)
        return facts

    async def retrieve_facts(self, request: RetrievalQuery) -> List[MemoryFact]:
        owner_id = str(request.owner_id or "")
        if not owner_id:
            return []
        query_tokens = _tokens(request.query)
        limit = max(1, int(request.max_results))
        started = time.perf_counter()
        try:
            if not query_tokens:
                facts = await self.store.list_active(owner_id=owner_id, scope=request.scope, limit=limit)
            else:
                facts = await self.store.search_active(
                    owner_id=owner_id,
                    scope=request.scope,
                    query=request.query,
                    limit=limit * 2,
                )
                if self.embed_fn is not None and len(facts) > 1:
                    facts = await self._maybe_rerank(request.query, facts)
                facts = facts[:limit]
                if not facts:
                    facts = await self.store.list_active(owner_id=owner_id, scope=request.scope, limit=limit)
                    facts = facts[:limit]
        except TimeoutError:
            self.metrics["timeout"] += 1
            return []
        except Exception:
            self.metrics["miss"] += 1
            return []
        # Authoritative filter: never return deleted/stale or wrong-owner rows.
        # The store already scopes by owner and excludes tombstones, but the
        # retriever re-checks so a slow index cannot resurrect them.
        fresh = [fact for fact in facts if not fact.deleted and fact.owner_id == owner_id]
        if len(fresh) != len(facts):
            self.metrics["truncated"] += 1
        if fresh:
            self.metrics["hit"] += 1
        else:
            self.metrics["miss"] += 1
        _ = started
        return fresh[:limit]

    async def retrieve_candidates(self, request: RetrievalQuery) -> List[RetrievalCandidate]:
        facts = await self.retrieve_facts(request)
        out: List[RetrievalCandidate] = []
        for fact in facts:
            out.append(_fact_to_candidate(fact, score=1.0, provenance=f"memory:{fact.scope}:{fact.id}"))
        return out

    async def _maybe_rerank(self, query: str, facts: List[MemoryFact]) -> List[MemoryFact]:
        if self.embed_fn is None:
            return facts
        try:
            import asyncio as _asyncio

            query_vec = await _asyncio.wait_for(
                self.embed_fn(query), timeout=self.rerank_timeout_ms / 1000.0
            )
            scored: List[tuple[float, MemoryFact]] = []
            for fact in facts:
                try:
                    vec = await _asyncio.wait_for(
                        self.embed_fn(str(fact.value or "")), timeout=self.rerank_timeout_ms / 1000.0
                    )
                except (TimeoutError, _asyncio.TimeoutError):
                    scored.append((0.0, fact))
                    continue
                score = _cosine(query_vec, vec)
                # Lexical overlap stays a floor so paraphrase gains never
                # drown exact name/number matches.
                lexical = len(_tokens(query) & _tokens(str(fact.value or ""))) * 0.1
                scored.append((score + lexical, fact))
            scored.sort(key=lambda row: -row[0])
            return [fact for _, fact in scored]
        except Exception:
            return facts

    def snapshot(self) -> Dict[str, Any]:
        return {"top_k": self.top_k, "metrics": dict(self.metrics)}


def _cosine(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left * norm_right)


@dataclass
class RAGDocument:
    doc_id: str
    version: str
    source: str
    text: str


class FixtureRAGRetriever:
    """Bounded RAG fixture retriever used to prove the seam, not production RAG.

    Ingestion/chunking stays outside the live path in tests. Instruction-like
    document text is treated as data and never executed as system instructions.
    """

    def __init__(self, documents: Iterable[RAGDocument], *, top_k: int = 4):
        self.documents: List[RAGDocument] = list(documents)
        self.top_k = max(1, int(top_k))

    async def retrieve_candidates(self, request: RetrievalQuery) -> List[RetrievalCandidate]:
        query_tokens = _tokens(request.query)
        scored: List[tuple[float, RAGDocument]] = []
        for doc in self.documents:
            overlap = len(query_tokens & _tokens(doc.text)) if query_tokens else 0
            if overlap > 0:
                scored.append((float(overlap), doc))
        scored.sort(key=lambda row: (-row[0], row[1].doc_id))
        out: List[RetrievalCandidate] = []
        for score, doc in scored[: max(1, int(request.max_results))][: self.top_k]:
            out.append(RetrievalCandidate(
                id=doc.doc_id,
                version=1,
                source=doc.source,
                score=score,
                observed_at="",
                value=doc.text[:1200],
                provenance=f"rag:{doc.source}:{doc.doc_id}:{doc.version}",
            ))
        return out
