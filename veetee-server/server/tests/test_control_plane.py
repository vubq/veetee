"""PostgreSQL-backed control-plane integration tests."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")


def isolated_database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("Control-plane tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


def _apply_login_rate_limit_migration(database: PostgresDatabase) -> None:
    migration = (
        Path(__file__).parents[1] / "migrations" / "004_login_rate_limit.sql"
    ).read_text(encoding="utf-8")
    with database.connection() as connection:
        connection.execute(migration)


@pytest.fixture
def persisted_client() -> TestClient:
    database = isolated_database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _apply_login_rate_limit_migration(database)
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_login_attempts, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
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
