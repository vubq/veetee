from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from veetee_voice_server.conversation.memory import (
    CompletedMemoryTurn,
    ConversationMemoryService,
    MemoryFactCandidate,
    MemoryPolicy,
    MemoryScope,
    memory_snapshot_from_payload,
)

pytestmark = pytest.mark.asyncio


class FakeMemoryBackend:
    def __init__(self) -> None:
        self.load_calls = 0
        self.load_payload: dict[str, Any] = {"messages": [], "memoryFacts": []}
        self.load_delay = 0.0
        self.message_batches: list[list[dict[str, Any]]] = []
        self.fact_batches: list[list[dict[str, Any]]] = []
        self.fail_writes = False
        self.write_seen = asyncio.Event()

    async def load_memory_context(self, scope: MemoryScope) -> dict[str, Any]:
        del scope
        self.load_calls += 1
        if self.load_delay:
            await asyncio.sleep(self.load_delay)
        return self.load_payload

    async def append_memory_messages(
        self, scope: MemoryScope, messages: list[dict[str, Any]]
    ) -> tuple[int, int]:
        del scope
        self.write_seen.set()
        if self.fail_writes:
            raise RuntimeError("manager unavailable")
        self.message_batches.append(messages)
        return len(messages), 0

    async def append_memory_facts(
        self, scope: MemoryScope, facts: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        del scope
        if self.fail_writes:
            raise RuntimeError("manager unavailable")
        self.fact_batches.append(facts)
        return len(facts), 0, 0


def active_policy(**overrides: object) -> MemoryPolicy:
    values: dict[str, object] = {
        "enabled": True,
        "consent": True,
        "store_messages": True,
        "store_facts": True,
    }
    values.update(overrides)
    return MemoryPolicy(**values)  # type: ignore[arg-type]


async def test_memory_is_opt_in_and_does_not_touch_manager_when_disabled() -> None:
    backend = FakeMemoryBackend()
    service = ConversationMemoryService(backend)
    await service.start()

    session = await service.open_device_session(
        device_id="device-1",
        agent_id="agent-1",
        config_version=3,
        policy=MemoryPolicy(enabled=True, consent=False, store_messages=True),
    )
    await service.close()

    assert session is None
    assert backend.load_calls == 0
    assert backend.message_batches == []


async def test_memory_load_timeout_degrades_to_an_empty_snapshot() -> None:
    backend = FakeMemoryBackend()
    backend.load_delay = 0.2
    service = ConversationMemoryService(backend, request_seconds=0.1)
    await service.start()

    session = await service.open_device_session(
        device_id="device-1",
        agent_id="agent-1",
        config_version=3,
        policy=active_policy(),
    )
    await service.close()

    assert session is not None
    assert session.snapshot.empty
    assert backend.load_calls == 1


async def test_memory_snapshot_reapplies_message_fact_and_total_character_bounds() -> None:
    policy = active_policy(
        max_messages=2,
        max_message_characters=128,
        max_context_characters=1_200,
        max_facts=1,
        max_fact_characters=64,
    )
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "old " * 40,
                "occurredAt": "2026-07-01T00:00:00Z",
            },
            {
                "role": "assistant",
                "content": "new assistant " * 20,
                "occurredAt": "2026-07-02T00:00:00Z",
            },
            {
                "role": "user",
                "content": "new user " * 20,
                "occurredAt": "2026-07-03T00:00:00Z",
            },
        ],
        "memoryFacts": [
            {
                "category": "preference",
                "key": "drink",
                "value": "cà phê sữa đá " * 20,
                "confidence": 2,
                "sourceSessionId": "source-session",
                "sourceTurnId": "source-turn",
                "expiresAt": "2026-08-01T00:00:00Z",
                "updatedAt": "2026-07-03T00:00:00Z",
            }
        ],
    }

    snapshot = memory_snapshot_from_payload(payload, policy)

    assert len(snapshot.messages) == 2
    assert snapshot.messages[-1].content.startswith("new user")
    assert all(len(item.content) <= 128 for item in snapshot.messages)
    assert len(snapshot.facts) == 1
    assert len(snapshot.facts[0].value) <= 64
    assert snapshot.facts[0].confidence == 1.0
    encoded = snapshot.untrusted_payload()
    assert encoded["boundary"] == "untrusted_cross_session_memory"
    assert "authority" in str(encoded["instruction"])
    assert len(json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))) <= 1_200


async def test_completed_turn_writes_idempotent_pair_and_structured_fact() -> None:
    backend = FakeMemoryBackend()
    service = ConversationMemoryService(backend)
    await service.start()
    policy = active_policy()
    session = await service.open_device_session(
        device_id="device-1",
        agent_id="agent-1",
        config_version=3,
        policy=policy,
    )
    assert session is not None
    turn = CompletedMemoryTurn(
        session_id="session-1",
        turn_id="turn-1",
        user_text="Tôi thích cà phê.",
        assistant_text="Tôi sẽ ghi nhớ.",
        fact_candidates=(
            MemoryFactCandidate("preference", "drink", "cà phê", 0.95, 999),
        ),
        occurred_at="2026-07-29T10:00:00+00:00",
    )

    assert session.record_completed_turn(turn)
    assert session.record_completed_turn(turn)
    await service.close()

    assert len(backend.message_batches) == 2
    assert [item["role"] for item in backend.message_batches[0]] == [
        "user",
        "assistant",
    ]
    assert (
        backend.message_batches[0][0]["idempotencyKey"]
        == backend.message_batches[1][0]["idempotencyKey"]
    )
    assert len(backend.fact_batches) == 2
    assert backend.fact_batches[0][0]["expiresInDays"] == policy.fact_retention_days
    assert (
        backend.fact_batches[0][0]["idempotencyKey"]
        == backend.fact_batches[1][0]["idempotencyKey"]
    )


async def test_manager_write_failure_never_blocks_or_fails_realtime_enqueue() -> None:
    backend = FakeMemoryBackend()
    backend.fail_writes = True
    service = ConversationMemoryService(backend, request_seconds=0.1)
    await service.start()
    session = await service.open_device_session(
        device_id="device-1",
        agent_id="agent-1",
        config_version=3,
        policy=active_policy(store_facts=False),
    )
    assert session is not None

    queued = session.record_completed_turn(
        CompletedMemoryTurn("session-1", "turn-1", "user", "assistant")
    )
    await asyncio.wait_for(backend.write_seen.wait(), timeout=0.5)
    await service.close()

    assert queued is True
    assert backend.message_batches == []
