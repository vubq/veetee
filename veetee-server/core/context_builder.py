from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from core.memory.retrieval import MemoryRetriever


class ContextBudgetError(ValueError):
    pass


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
        self.last_budget: Dict[str, Any] = {}

    @staticmethod
    def _serialized_chars(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))

    @classmethod
    def _message_groups(cls, messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Group turns so trimming never splits a user/tool-call/result unit."""
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "")
            if role == "system":
                if current:
                    groups.append(current)
                    current = []
                groups.append([message])
                continue
            if role == "user" and current:
                groups.append(current)
                current = []
            current.append(message)
        if current:
            groups.append(current)
        return groups

    def fit_to_budget(
        self,
        messages: List[Dict[str, Any]],
        *,
        system_messages: List[str],
        tools: List[Dict[str, Any]],
        max_context_tokens: int,
        reserve_output_tokens: int,
        chars_per_token: int,
    ) -> List[Dict[str, Any]]:
        """Bound the complete LLM request using a documented char estimate.

        The latest conversational group is mandatory. Older history and
        memory/system enrichment are dropped only as whole groups.
        """
        chars_per_token = max(1, int(chars_per_token))
        max_context_tokens = max(1, int(max_context_tokens))
        reserve_output_tokens = max(0, int(reserve_output_tokens))
        total_budget_chars = max_context_tokens * chars_per_token
        reserve_chars = reserve_output_tokens * chars_per_token
        system_chars = sum(self._serialized_chars({"role": "system", "content": text}) for text in system_messages if text)
        tools_chars = self._serialized_chars(tools) if tools else 0
        available_message_chars = total_budget_chars - reserve_chars - system_chars - tools_chars
        groups = self._message_groups(messages)
        if not groups:
            self.last_budget = {
                "estimated": True,
                "max_context_tokens": max_context_tokens,
                "chars_per_token": chars_per_token,
                "reserve_output_tokens": reserve_output_tokens,
                "system_chars": system_chars,
                "tools_chars": tools_chars,
                "message_budget_chars": max(0, available_message_chars),
                "message_chars": 0,
                "groups_total": 0,
                "groups_kept": 0,
                "trimmed_groups": 0,
            }
            return []
        if available_message_chars <= 0:
            raise ContextBudgetError("fixed system/tool/output budget exhausts the LLM context")

        group_costs = [self._serialized_chars(group) for group in groups]
        latest_cost = group_costs[-1]
        if latest_cost > available_message_chars:
            raise ContextBudgetError("current user/tool turn exceeds the configured LLM context budget")

        selected_indexes = [len(groups) - 1]
        used = latest_cost
        for index in range(len(groups) - 2, -1, -1):
            cost = group_costs[index]
            if used + cost <= available_message_chars:
                selected_indexes.append(index)
                used += cost
        selected_indexes.sort()
        fitted = [message for index in selected_indexes for message in groups[index]]
        self.last_budget = {
            "estimated": True,
            "max_context_tokens": max_context_tokens,
            "chars_per_token": chars_per_token,
            "reserve_output_tokens": reserve_output_tokens,
            "system_chars": system_chars,
            "tools_chars": tools_chars,
            "message_budget_chars": available_message_chars,
            "message_chars": used,
            "groups_total": len(groups),
            "groups_kept": len(selected_indexes),
            "trimmed_groups": len(groups) - len(selected_indexes),
        }
        return fitted

    async def build(
        self,
        messages: List[Dict[str, Any]],
        *,
        query: str,
        owner_id: Optional[str],
        session_memory: List[str],
    ) -> List[Dict[str, Any]]:
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
