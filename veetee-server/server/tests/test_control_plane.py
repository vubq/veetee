"""PostgreSQL-backed control-plane integration tests."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.dialogue.history import DialogueHistory
from veetee_server.dialogue.recorder import ConversationRecorder
from veetee_server.persistence import (
    ConversationRepository,
    DatabaseConfig,
    PostgresDatabase,
    purge_expired_conversations,
)
from veetee_server.persistence.repository import hash_password

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")


def isolated_database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("Control-plane tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


def _apply_migrations(database: PostgresDatabase) -> None:
    migrations_dir = Path(__file__).parents[1] / "migrations"
    for name in (
        "001_control_plane.sql",
        "002_runtime_control_plane.sql",
        "003_device_activation_ota.sql",
        "004_login_rate_limit.sql",
        "005_m6_foundation_providers.sql",
        "006_m6_agent_lifecycle_history.sql",
        "007_m6_knowledge_rag.sql",
        "008_m6_corrections_context.sql",
        "009_m6_tool_integrations.sql",
    ):
        with database.connection() as connection:
            connection.execute((migrations_dir / name).read_text(encoding="utf-8"))


@pytest.fixture
def persisted_client() -> TestClient:
    database = isolated_database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _apply_migrations(database)
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_agent_integration_permissions, veetee_external_endpoints, "
            "veetee_agent_context_providers, veetee_correction_rules, "
            "veetee_correction_sets, veetee_agent_datasets, veetee_chunks, "
            "veetee_documents, veetee_datasets, veetee_conversation_turns, "
            "veetee_agent_snapshots, "
            "veetee_agent_tag_links, veetee_agent_tags, veetee_agent_templates, "
            "veetee_login_attempts, veetee_audit_events, veetee_memories, "
            "veetee_providers, veetee_conversations, veetee_devices, veetee_agents, "
            "veetee_sessions, veetee_users CASCADE"
        )
    # Restore the code-level catalog seed rows after the tenant-scoped reset.
    with database.connection() as connection:
        connection.execute(
            (Path(__file__).parents[1] / "migrations" / "005_m6_foundation_providers.sql")
            .read_text(encoding="utf-8")
        )
    settings = Settings(
        app_name="test-control-plane",
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DATABASE_DSN,
        ota_public_base_url="http://ota.example.test",
        bootstrap_admin_email="owner@example.test",
        bootstrap_admin_password="a-test-password-long-enough",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_agent_crud_auth_and_optimistic_concurrency(persisted_client: TestClient) -> None:
    client = persisted_client
    assert client.get("/api/v1/control/agents").status_code == 401

    login = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = client.post(
        "/api/v1/control/agents",
        headers=headers,
        json={
            "name": "Trợ lý kể chuyện",
            "role_prompt": "Kể chuyện lịch sử bằng tiếng Việt.",
            "personality": "Điềm tĩnh",
            "detail_level": "adaptive",
        },
    )
    assert created.status_code == 201
    agent = created.json()
    assert agent["version"] == 1
    assert agent["role_prompt"] == "Kể chuyện lịch sử bằng tiếng Việt."

    listing = client.get("/api/v1/control/agents", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    stale = {**agent, "name": "Tên cũ", "expected_version": 1}
    updated = client.put(
        f"/api/v1/control/agents/{agent['id']}",
        headers=headers,
        json={**agent, "name": "Tên mới", "expected_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.put(
        f"/api/v1/control/agents/{agent['id']}", headers=headers, json=stale
    ).status_code == 409

    assert (
        client.delete(
            f"/api/v1/control/agents/{agent['id']}", headers=headers
        ).status_code
        == 204
    )


def test_migration_is_idempotent() -> None:
    migration = Path(__file__).parents[1] / "migrations/001_control_plane.sql"
    assert migration.exists()
    database = isolated_database()
    with database.connection() as connection:
        connection.execute(migration.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT count(*) FROM veetee_schema_migrations WHERE version = '001_control_plane'"
        ).fetchone()[0] == 1


def test_memory_crud_and_audit_are_tenant_scoped(persisted_client: TestClient) -> None:
    login = persisted_client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = persisted_client.post(
        "/api/v1/control/memories",
        headers=headers,
        json={
            "kind": "profile",
            "content": "Người dùng thích nghe kể chuyện lịch sử.",
            "provenance": "user_explicit",
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]
    memories = persisted_client.get("/api/v1/control/memories", headers=headers).json()
    assert memories[0]["id"] == memory_id
    assert persisted_client.delete(
        f"/api/v1/control/memories/{memory_id}", headers=headers
    ).status_code == 204
    with isolated_database().connection() as connection:
        actions = connection.execute(
            "SELECT action FROM veetee_audit_events "
            "WHERE action LIKE 'memory.%' ORDER BY created_at"
        ).fetchall()
    assert [row[0] for row in actions] == ["memory.create", "memory.forget"]


def test_logout_revokes_session(persisted_client: TestClient) -> None:
    login = persisted_client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    agents_res = persisted_client.get("/api/v1/control/agents", headers=headers)
    assert agents_res.status_code == 200

    logout_res = persisted_client.post("/api/v1/control/auth/logout", headers=headers)
    assert logout_res.status_code == 204

    agents_res_after = persisted_client.get("/api/v1/control/agents", headers=headers)
    assert agents_res_after.status_code == 401
    with isolated_database().connection() as connection:
        audit = connection.execute(
            "SELECT action, resource_type, resource_id FROM veetee_audit_events "
            "WHERE action = 'session.logout'"
        ).fetchone()
    assert audit == ("session.logout", "session", "current")


def test_duplicate_agent_create_and_rename_return_conflict(
    persisted_client: TestClient,
) -> None:
    client = persisted_client
    login = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    first = client.post("/api/v1/control/agents", headers=headers,
                        json={"name": "Trợ lý chung"})
    assert first.status_code == 201

    duplicate = client.post("/api/v1/control/agents", headers=headers,
                            json={"name": "Trợ lý chung"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Agent name already exists"

    other = client.post("/api/v1/control/agents", headers=headers,
                        json={"name": "Trợ lý khác"})
    assert other.status_code == 201
    rename_conflict = client.put(
        f"/api/v1/control/agents/{other.json()['id']}",
        headers=headers,
        json={
            "name": "Trợ lý chung",
            "expected_version": other.json()["version"],
        },
    )
    assert rename_conflict.status_code == 409
    assert rename_conflict.json()["detail"] == "Agent name already exists"

    # Neither the conflicting rename nor the duplicate create may mutate data.
    listing = {agent["name"]: agent["version"]
               for agent in client.get("/api/v1/control/agents", headers=headers).json()}
    assert listing == {"Trợ lý chung": 1, "Trợ lý khác": 1}


def test_agent_model_id_must_match_backend_catalog(persisted_client: TestClient) -> None:
    client = persisted_client
    login = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    providers = {
        model
        for provider in client.get("/api/v1/control/providers", headers=headers).json()
        for model in provider["models"]
    }
    assert providers  # shared backend catalog is non-empty via provider listing

    created = client.post(
        "/api/v1/control/agents", headers=headers, json={"name": "Không chọn model"}
    )
    assert created.status_code == 201
    assert created.json()["model_id"] == ""

    allowed = client.post(
        "/api/v1/control/agents",
        headers=headers,
        json={"name": "Model hợp lệ", "model_id": "groq/openai/gpt-oss-120b"},
    )
    assert allowed.status_code == 201

    rejected_create = client.post(
        "/api/v1/control/agents",
        headers=headers,
        json={"name": "Sai model", "model_id": "gpt-4o"},
    )
    assert rejected_create.status_code == 422
    assert rejected_create.json()["detail"] == (
        "model_id must match the backend provider catalog"
    )

    rejected_update = client.put(
        f"/api/v1/control/agents/{created.json()['id']}",
        headers=headers,
        json={"name": "Không chọn model",
              "model_id": "groq/khong-ton-tai",
              "expected_version": 1},
    )
    assert rejected_update.status_code == 422

    rejected_non_llm_update = client.put(
        f"/api/v1/control/agents/{created.json()['id']}",
        headers=headers,
        json={"name": "Không chọn model",
              "model_id": "mad1999/pho-whisper-small-ct2",
              "expected_version": 1},
    )
    assert rejected_non_llm_update.status_code == 422

    accepted_update = client.put(
        f"/api/v1/control/agents/{created.json()['id']}",
        headers=headers,
        json={"name": "Không chọn model",
              "model_id": "groq/qwen/qwen3.6-27b",
              "expected_version": 1},
    )
    assert accepted_update.status_code == 200
    assert accepted_update.json()["model_id"] == "groq/qwen/qwen3.6-27b"


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/control/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_provider_management_requires_admin_and_protects_default(
    persisted_client: TestClient,
) -> None:
    owner_headers = _login(
        persisted_client, "owner@example.test", "a-test-password-long-enough"
    )
    listing = persisted_client.get("/api/v1/control/providers", headers=owner_headers)
    assert listing.status_code == 200
    llm = next(item for item in listing.json() if item["kind"] == "llm")
    assert llm == {
        "kind": "llm",
        "provider_id": "omniroute",
        "models": ["groq/openai/gpt-oss-120b", "groq/qwen/qwen3.6-27b"],
        "enabled": True,
        "default": True,
        "is_default": True,
        "health": {"status": "unknown", "details": "Runtime is not active"},
        "config_version": 1,
        "secret_configurable": False,
    }
    for provider in listing.json():
        assert not ({"secret", "secret_reference", "api_key", "token"} & provider.keys())
    assert persisted_client.patch(
        "/api/v1/control/providers/llm/omniroute",
        headers=owner_headers,
        json={"enabled": True, "expected_version": 1},
    ).status_code == 403

    admin_email = "admin@example.test"
    admin_password = "another-test-password"
    with isolated_database().connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'admin')",
            (uuid4(), admin_email, hash_password(admin_password)),
        )
    admin_headers = _login(persisted_client, admin_email, admin_password)

    protected = persisted_client.patch(
        "/api/v1/control/providers/llm/omniroute",
        headers=admin_headers,
        json={"enabled": False, "expected_version": 1},
    )
    assert protected.status_code == 409
    stale = persisted_client.patch(
        "/api/v1/control/providers/llm/omniroute",
        headers=admin_headers,
        json={"enabled": True, "expected_version": 99},
    )
    assert stale.status_code == 409

    updated = persisted_client.patch(
        "/api/v1/control/providers/llm/omniroute",
        headers=admin_headers,
        json={"enabled": True, "expected_version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["config_version"] == 2
    assert persisted_client.patch(
        "/api/v1/control/providers/llm/omniroute",
        headers=admin_headers,
        json={"enabled": True, "expected_version": 1},
    ).status_code == 409

    health = persisted_client.post(
        "/api/v1/control/providers/llm/omniroute/health-check",
        headers=admin_headers,
    )
    assert health.status_code == 200
    assert health.json()["health"]["status"] == "unknown"
    with isolated_database().connection() as connection:
        actions = connection.execute(
            "SELECT action, metadata FROM veetee_audit_events "
            "WHERE action LIKE 'provider.%' ORDER BY created_at"
        ).fetchall()
    assert [row[0] for row in actions] == ["provider.update", "provider.health_check"]
    assert all("secret" not in str(row[1]).lower() for row in actions)


def test_suspended_user_cannot_login_or_reuse_existing_session(
    persisted_client: TestClient,
) -> None:
    headers = _login(
        persisted_client, "owner@example.test", "a-test-password-long-enough"
    )
    with isolated_database().connection() as connection:
        connection.execute(
            "UPDATE veetee_users SET status = 'suspended', version = version + 1 "
            "WHERE email = 'owner@example.test'"
        )
    assert persisted_client.get("/api/v1/control/agents", headers=headers).status_code == 401
    assert persisted_client.post(
        "/api/v1/control/auth/login",
        json={
            "email": "owner@example.test",
            "password": "a-test-password-long-enough",
        },
    ).status_code == 401


def test_m6_provider_migration_constraints_and_idempotency() -> None:
    database = isolated_database()
    _apply_migrations(database)
    _apply_migrations(database)
    with database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM veetee_schema_migrations "
            "WHERE version = '005_m6_foundation_providers'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM veetee_providers WHERE enabled AND is_default"
        ).fetchone()[0] == 3
        owner = connection.execute(
            "SELECT id, email FROM veetee_users WHERE email = 'owner@example.test'"
        ).fetchone()
        assert owner is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE veetee_providers SET enabled = false "
                    "WHERE provider_kind = 'llm' AND provider_id = 'omniroute'"
                )
        assert connection.execute(
            "SELECT email FROM veetee_users WHERE id = %s", (owner[0],)
        ).fetchone()[0] == owner[1]


def test_agent_template_snapshot_restore_and_tag_are_tenant_scoped(
    persisted_client: TestClient,
) -> None:
    owner_headers = _login(
        persisted_client, "owner@example.test", "a-test-password-long-enough"
    )
    template = persisted_client.post(
        "/api/v1/control/templates",
        headers=owner_headers,
        json={
            "name": "Kể chuyện",
            "description": "Mẫu tenant",
            "config": {"role_prompt": "Vai trò ban đầu", "personality": "Điềm tĩnh"},
        },
    )
    assert template.status_code == 201
    created = persisted_client.post(
        f"/api/v1/control/templates/{template.json()['id']}/agents",
        headers=owner_headers,
        json={"name": "Trợ lý từ mẫu"},
    )
    assert created.status_code == 201
    agent = created.json()
    assert agent["role_prompt"] == "Vai trò ban đầu"

    snapshot = persisted_client.post(
        f"/api/v1/control/agents/{agent['id']}/snapshots",
        headers=owner_headers,
        json={"reason": "manual"},
    )
    assert snapshot.status_code == 201
    assert len(snapshot.json()["checksum"]) == 64
    same_snapshot = persisted_client.post(
        f"/api/v1/control/agents/{agent['id']}/snapshots",
        headers=owner_headers,
        json={"reason": "manual"},
    )
    assert same_snapshot.json()["checksum"] == snapshot.json()["checksum"]

    updated = persisted_client.put(
        f"/api/v1/control/agents/{agent['id']}",
        headers=owner_headers,
        json={**agent, "role_prompt": "Vai trò mới", "expected_version": 1},
    )
    assert updated.status_code == 200
    restore_url = (
        f"/api/v1/control/agents/{agent['id']}/snapshots/{snapshot.json()['id']}/restore"
    )
    assert persisted_client.post(
        restore_url,
        headers=owner_headers,
        json={"expected_agent_version": 1},
    ).status_code == 409
    restored = persisted_client.post(
        restore_url,
        headers=owner_headers,
        json={"expected_agent_version": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 3
    assert restored.json()["role_prompt"] == "Vai trò ban đầu"
    snapshots = persisted_client.get(
        f"/api/v1/control/agents/{agent['id']}/snapshots", headers=owner_headers
    ).json()
    assert {item["reason"] for item in snapshots} == {"manual", "pre_restore"}

    tag = persisted_client.post(
        "/api/v1/control/tags", headers=owner_headers, json={"name": "gia đình"}
    )
    assert tag.status_code == 201
    assert persisted_client.put(
        f"/api/v1/control/agents/{agent['id']}/tags/{tag.json()['id']}",
        headers=owner_headers,
    ).status_code == 204

    second_email = "tenant-two@example.test"
    second_password = "tenant-two-password"
    with isolated_database().connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'owner')",
            (uuid4(), second_email, hash_password(second_password)),
        )
    second_headers = _login(persisted_client, second_email, second_password)
    assert persisted_client.get(
        f"/api/v1/control/agents/{agent['id']}/snapshots", headers=second_headers
    ).json() == []
    assert persisted_client.put(
        f"/api/v1/control/agents/{agent['id']}/tags/{tag.json()['id']}",
        headers=second_headers,
    ).status_code == 404
    assert persisted_client.get(
        "/api/v1/control/templates", headers=second_headers
    ).json() == []


def test_conversation_consent_export_delete_and_retention(
    persisted_client: TestClient,
) -> None:
    headers = _login(
        persisted_client, "owner@example.test", "a-test-password-long-enough"
    )
    with isolated_database().connection() as connection:
        owner_id = connection.execute(
            "SELECT id FROM veetee_users WHERE email = 'owner@example.test'"
        ).fetchone()[0]
    repository = ConversationRepository(isolated_database())
    recorder = ConversationRecorder(repository, retention_days=30)
    history = DialogueHistory()
    user_turn = history.add_user_turn(
        "  Xin Chao  ", "xin chào", "Xin chào", {"source": "asr"}
    )

    opted_out = recorder.begin(owner_id, transcript_consent=False)
    assert recorder.record_turn(opted_out, user_turn) is None
    assert repository.list_conversations(owner_id) == []

    context = recorder.begin(
        owner_id,
        title="Hội thoại có đồng ý",
        transcript_consent=True,
        consent_version="transcript-v1",
    )
    stored_user = recorder.record_turn(context, user_turn)
    assert stored_user is not None
    assert stored_user.raw_transcript == "Xin Chao"
    assert stored_user.normalized_text == "xin chào"
    assert stored_user.model_text == "Xin chào"
    assistant = recorder.record_turn(
        context, history.add_assistant_turn("Chào bạn", metadata={"model": "test"})
    )
    assert assistant is not None and assistant.ordinal == 2
    assert recorder.record_turn(context, user_turn).id == stored_user.id

    conversation_id = context.conversation_id
    assert conversation_id is not None
    turns = persisted_client.get(
        f"/api/v1/control/conversations/{conversation_id}/turns", headers=headers
    )
    assert turns.status_code == 200
    assert [item["ordinal"] for item in turns.json()] == [1, 2]

    exported = persisted_client.get(
        f"/api/v1/control/conversations/{conversation_id}/export", headers=headers
    )
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    payload = json.loads(exported.text)
    assert payload["schema"] == "veetee.conversation.export.v1"
    assert len(payload["turns"]) == 2
    export_text = exported.text.lower()
    assert "password" not in export_text and "bearer" not in export_text

    assert persisted_client.delete(
        f"/api/v1/control/conversations/{conversation_id}", headers=headers
    ).status_code == 204
    assert repository.get(owner_id, conversation_id) is None
    with isolated_database().connection() as connection:
        audit = connection.execute(
            "SELECT metadata FROM veetee_audit_events "
            "WHERE action = 'conversation.hard_delete'"
        ).fetchone()[0]
    assert audit == {"turns_removed": 2}
    assert "xin chào" not in str(audit).lower()

    expiring = repository.get_or_create(owner_id, title="Sắp hết hạn")
    with isolated_database().connection() as connection:
        connection.execute(
            "UPDATE veetee_conversations SET retention_until = %s WHERE id = %s",
            (datetime.now(UTC) - timedelta(seconds=1), expiring.id),
        )
    assert purge_expired_conversations(isolated_database(), batch_size=1) == 1
    assert purge_expired_conversations(isolated_database(), batch_size=1) == 0
