from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from core.memory.retrieval import MemoryRetriever


class ContextBuilder:
    def __init__(
        self,
        retriever: Optional[MemoryRetriever] = None,
        *,
        lookup_timeout_ms: int = 10,
        top_k: int = 6,
        max_memory_chars: int = 2400,
    ):
        self.retriever = retriever
        self.lookup_timeout_ms = max(1, int(lookup_timeout_ms))
        self.top_k = max(1, int(top_k))
        self.max_memory_chars = max(200, int(max_memory_chars))

    async def build(
        self,
        messages: List[Dict[str, str]],
        *,
        query: str,
        owner_id: Optional[str],
        session_memory: List[str],
    ) -> List[Dict[str, str]]:
        facts = list(session_memory[-self.top_k :])
        if owner_id and self.retriever is not None:
            try:
                durable = await asyncio.wait_for(
                    self.retriever.retrieve(owner_id=owner_id, scope="personal", query=query),
                    timeout=self.lookup_timeout_ms / 1000.0,
                )
                facts.extend(fact.value for fact in durable)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Memory is a best-effort local enrichment path. A locked or
                # unavailable DB must degrade the turn to normal chat instead
                # of breaking the realtime response pipeline.
                pass
        unique = []
        for fact in facts:
            cleaned = " ".join(str(fact).split())
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        if not unique:
            return list(messages)
        memory_text = "\n".join(f"- {item}" for item in unique[: self.top_k])[: self.max_memory_chars]
        memory_message = {
            "role": "system",
            "content": (
                "Memory đã được server xác thực cho lượt này. Chỉ dùng khi liên quan; "
                "không suy diễn thêm và không tiết lộ namespace/kỹ thuật lưu trữ:\n" + memory_text
            ),
        }
        # Keep the current user message last.
        if messages and messages[-1].get("role") == "user":
            return [*messages[:-1], memory_message, messages[-1]]
        return [*messages, memory_message]
