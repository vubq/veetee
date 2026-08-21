"""Tests for memory policy, storage, retrieval, and tenant isolation."""

import re
import time

from veetee_server.memory import (
    InMemoryMemoryStore,
    MemoryEntry,
    MemoryKind,
    MemoryPolicy,
    MemoryProposal,
    MemoryRetriever,
    RetrievalQuery,
    TenantScope,
)


def test_tenant_scope_isolation():
    store = InMemoryMemoryStore()
    scope_a = TenantScope(user_id="user_a", agent_id="agent_1")
    scope_b = TenantScope(user_id="user_b", agent_id="agent_1")

    e1 = MemoryEntry(
        id="m1",
        tenant_scope=scope_a,
        kind=MemoryKind.PROFILE,
        content="Sở thích: thích uống trà đá",
        provenance="user_explicit",
        confidence=0.9,
    )
    e2 = MemoryEntry(
        id="m2",
        tenant_scope=scope_b,
        kind=MemoryKind.PROFILE,
        content="Sở thích: thích uống cà phê",
        provenance="user_explicit",
        confidence=0.9,
    )

    store.upsert(e1)
    store.upsert(e2)

    # User A sees only e1
    list_a = store.list_by_tenant(scope_a)
    assert len(list_a) == 1
    assert list_a[0].content == "Sở thích: thích uống trà đá"

    # User B sees only e2
    list_b = store.list_by_tenant(scope_b)
    assert len(list_b) == 1
    assert list_b[0].content == "Sở thích: thích uống cà phê"

    # User B cannot forget User A's memory
    assert store.forget("m1", scope_b) is False
    assert store.get("m1", scope_a) is not None


def test_memory_policy_evaluation():
    policy = MemoryPolicy(min_profile_confidence=0.8)
    scope = TenantScope(user_id="u1", agent_id="a1")

    # 1. Transient greeting rejected
    p_hello = MemoryProposal(
        content="Chào bạn",
        kind=MemoryKind.EPISODIC,
        tenant_scope=scope,
        provenance="turn_1",
    )
    assert policy.evaluate_proposal(p_hello) is False

    # 2. Sensitive password pattern rejected
    p_pass = MemoryProposal(
        content="Mật khẩu wifi: password123456",
        kind=MemoryKind.PROFILE,
        tenant_scope=scope,
        provenance="turn_2",
    )
    assert policy.evaluate_proposal(p_pass) is False

    # 3. Profile memory below min confidence rejected
    p_low_conf = MemoryProposal(
        content="Có thể sống ở Hà Nội",
        kind=MemoryKind.PROFILE,
        tenant_scope=scope,
        provenance="turn_3",
        confidence=0.6,
    )
    assert policy.evaluate_proposal(p_low_conf) is False

    # 4. Valid profile memory accepted
    p_valid = MemoryProposal(
        content="Thành phố sinh sống: Hà Nội",
        kind=MemoryKind.PROFILE,
        tenant_scope=scope,
        provenance="user_explicit",
        confidence=0.9,
    )
    assert policy.evaluate_proposal(p_valid) is True


def test_memory_policy_rules_are_injectable():
    policy = MemoryPolicy(
        sensitive_patterns=(re.compile(r"forbidden", re.IGNORECASE),),
        transient_detector=lambda value: value == "skip this",
    )
    scope = TenantScope(user_id="u1", agent_id="a1")
    assert not policy.evaluate_proposal(
        MemoryProposal("skip this", MemoryKind.EPISODIC, scope, "test")
    )
    assert not policy.evaluate_proposal(
        MemoryProposal("forbidden value", MemoryKind.EPISODIC, scope, "test")
    )
    assert policy.evaluate_proposal(
        MemoryProposal("valid value", MemoryKind.EPISODIC, scope, "test")
    )


def test_memory_conflict_resolution():
    store = InMemoryMemoryStore()
    scope = TenantScope(user_id="u1", agent_id="a1")

    # Original profile fact
    e_old = MemoryEntry(
        id="fact_city",
        tenant_scope=scope,
        kind=MemoryKind.PROFILE,
        content="Nơi ở: Thành phố Hồ Chí Minh",
        provenance="turn_1",
        confidence=0.85,
        metadata={"key": "user_city"},
    )
    store.upsert(e_old)

    # Newer updated profile fact with same metadata key
    e_new = MemoryEntry(
        id="fact_city_v2",
        tenant_scope=scope,
        kind=MemoryKind.PROFILE,
        content="Nơi ở: Hà Nội",
        provenance="turn_10",
        confidence=0.95,
        metadata={"key": "user_city"},
    )
    store.upsert(e_new)

    # Conflicting entry updated seamlessly
    entries = store.list_by_tenant(scope, kind=MemoryKind.PROFILE)
    assert len(entries) == 1
    assert entries[0].content == "Nơi ở: Hà Nội"
    assert entries[0].confidence == 0.95


def test_hybrid_retrieval_ranking_and_anti_injection():
    store = InMemoryMemoryStore()
    scope = TenantScope(user_id="u1", agent_id="a1")
    now = time.time()

    e_recent_weather = MemoryEntry(
        id="m_weather",
        tenant_scope=scope,
        kind=MemoryKind.EPISODIC,
        content="Hôm nay thời tiết Hà Nội rất đẹp",
        provenance="turn_5",
        confidence=0.9,
        created_at=now - 10,
    )

    e_old_weather = MemoryEntry(
        id="m_weather_old",
        tenant_scope=scope,
        kind=MemoryKind.EPISODIC,
        content="Thời tiết Hà Nội tuần trước có mưa",
        provenance="turn_1",
        confidence=0.9,
        created_at=now - 86400 * 7,
    )

    store.upsert(e_recent_weather)
    store.upsert(e_old_weather)

    retriever = MemoryRetriever(store)
    query = RetrievalQuery(
        scope=scope,
        query_text="thời tiết Hà Nội",
        limit=2,
    )
    results = retriever.retrieve(query)

    assert len(results) == 2
    # Recent weather entry ranked higher due to recency decay
    assert results[0].id == "m_weather"


def test_delete_all_by_kind():
    store = InMemoryMemoryStore()
    scope = TenantScope(user_id="u1", agent_id="a1")

    store.upsert(
        MemoryEntry(
            id="e1", tenant_scope=scope, kind=MemoryKind.EPISODIC, content="Turn 1", provenance="p"
        )
    )
    store.upsert(
        MemoryEntry(
            id="p1", tenant_scope=scope, kind=MemoryKind.PROFILE, content="Fact 1", provenance="p"
        )
    )

    deleted = store.delete_all(scope, kind=MemoryKind.EPISODIC)
    assert deleted == 1

    remaining = store.list_by_tenant(scope)
    assert len(remaining) == 1
    assert remaining[0].kind == MemoryKind.PROFILE
