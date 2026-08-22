"""PostgreSQL-backed control-plane integration tests."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")


def isolated_database() -> PostgresDatabase:
    if conninfo_to_dict(TEST_DATABASE_DSN).get("dbname") != "veetee_test":
        raise RuntimeError("Control-plane tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


@pytest.fixture
def persisted_client() -> TestClient:
    database = isolated_database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_audit_events, veetee_memories, veetee_devices, "
            "veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
    settings = Settings(
        app_name="test-control-plane",
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DATABASE_DSN,
        bootstrap_admin_email="owner@example.test",
        bootstrap_admin_password="a-test-password-long-enough",
        activation_secret="activation-test-secret-32-characters",
        device_jwt_secret="device-token-test-secret-32-characters",
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
    assert (
        client.put(f"/api/v1/control/agents/{agent['id']}", headers=headers, json=stale).status_code
        == 409
    )

    assert (
        client.delete(f"/api/v1/control/agents/{agent['id']}", headers=headers).status_code == 204
    )


def test_migration_is_idempotent() -> None:
    migration = Path(__file__).parents[1] / "migrations/001_control_plane.sql"
    assert migration.exists()
    database = isolated_database()
    with database.connection() as connection:
        connection.execute(migration.read_text(encoding="utf-8"))
        assert (
            connection.execute(
                "SELECT count(*) FROM veetee_schema_migrations WHERE version = '001_control_plane'"
            ).fetchone()[0]
            == 1
        )


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
    assert (
        persisted_client.delete(
            f"/api/v1/control/memories/{memory_id}", headers=headers
        ).status_code
        == 204
    )
    with isolated_database().connection() as connection:
        actions = connection.execute(
            "SELECT action FROM veetee_audit_events ORDER BY created_at"
        ).fetchall()
    assert [row[0] for row in actions] == [
        "identity.bootstrap_admin_created",
        "memory.create",
        "memory.forget",
    ]
