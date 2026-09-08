from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

from core.memory.models import MemoryFact
from core.memory.store import MemoryStore


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1}


class MemoryRetriever:
    def __init__(self, store: MemoryStore, *, top_k: int = 6):
        self.store = store
        self.top_k = max(1, int(top_k))

    async def retrieve(self, *, owner_id: str, scope: str, query: str) -> List[MemoryFact]:
        query_tokens = _tokens(query)
        if not query_tokens:
            facts = await self.store.list_active(owner_id=owner_id, scope=scope, limit=self.top_k)
            return facts[: self.top_k]
        facts = await self.store.search_active(
            owner_id=owner_id,
            scope=scope,
            query=query,
            limit=self.top_k,
        )
        if facts:
            return facts
        return await self.store.list_active(owner_id=owner_id, scope=scope, limit=self.top_k)
