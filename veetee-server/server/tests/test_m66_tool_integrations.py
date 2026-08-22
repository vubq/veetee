"""PostgreSQL-backed M6.6 integration endpoint and permission tests."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase
from veetee_server.persistence.repository import hash_password

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"


class FakeExternalClient:
    async def list_tools(
        self, url: str, *, auth_header_env: str | None = None
    ) -> list[dict[str, str]]:
        assert url.startswith("https://")
        assert auth_header_env == "MCP_TEST_TOKEN"
        return [{"name": "remote.echo", "description": "Echo"}]

    async def call_tool(
        self,
        url: str,
        name: str,
        arguments: dict[str, object],
        *,
        auth_header_env: str | None = None,
    ) -> dict[str, object]:
        assert url.startswith("https://") and name == "remote.echo"
        assert auth_header_env == "MCP_TEST_TOKEN"
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def _database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("M6.6 tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


def _apply_migrations(database: PostgresDatabase) -> None:
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
            connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))


@pytest.fixture
def integration_client() -> TestClient:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _apply_migrations(database)
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_agent_integration_permissions, veetee_external_endpoints, "
            "veetee_agent_context_providers, veetee_correction_rules, "
            "veetee_correction_sets, veetee_agent_datasets, veetee_chunks, "
            "veetee_documents, veetee_datasets, veetee_conversation_turns, "
            "veetee_agent_snapshots, veetee_agent_tag_links, veetee_agent_tags, "
            "veetee_agent_templates, veetee_login_attempts, veetee_audit_events, "
            "veetee_memories, veetee_providers, veetee_conversations, veetee_devices, "
            "veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
        connection.execute(
            (MIGRATIONS / "005_m6_foundation_providers.sql").read_text(encoding="utf-8")
        )
    settings = Settings(
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DATABASE_DSN,
        ota_public_base_url="http://ota.example.test",
        bootstrap_admin_email="owner@example.test",
        bootstrap_admin_password="a-test-password-long-enough",
        tool_external_allowed_hosts=["mcp.example.test"],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.external_mcp_client = FakeExternalClient()
        yield client


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/control/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_external_endpoint_permission_call_rate_limit_and_audit_are_safe(
    integration_client: TestClient,
) -> None:
    client = integration_client
    headers = _login(client, "owner@example.test", "a-test-password-long-enough")
    agent = client.post(
        "/api/v1/control/agents", headers=headers, json={"name": "Tool Agent"}
    ).json()
    created = client.post(
        "/api/v1/control/integrations/endpoints",
        headers=headers,
        json={
            "name": "Remote MCP",
            "url": "https://mcp.example.test/rpc",
            "auth_header_env": "MCP_TEST_TOKEN",
        },
    )
    assert created.status_code == 201
    endpoint_id = created.json()["id"]

    denied = client.post(
        f"/api/v1/control/integrations/endpoints/{endpoint_id}/test/list",
        headers=headers,
        json={"agent_id": agent["id"]},
    )
    assert denied.status_code == 403
    granted = client.put(
        f"/api/v1/control/agents/{agent['id']}/integration-permissions/{endpoint_id}",
        headers=headers,
        json={
            "can_list": True,
            "can_call": True,
            "rate_limit_calls": 2,
            "rate_limit_window_seconds": 60,
        },
    )
    assert granted.status_code == 200
    listed = client.post(
        f"/api/v1/control/integrations/endpoints/{endpoint_id}/test/list",
        headers=headers,
        json={"agent_id": agent["id"]},
    )
    assert listed.json()["tools"] == [{"name": "remote.echo", "description": "Echo"}]
    called = client.post(
        f"/api/v1/control/integrations/endpoints/{endpoint_id}/test/call",
        headers=headers,
        json={
            "agent_id": agent["id"],
            "tool_name": "remote.echo",
            "arguments": {"password": "must-not-be-audited"},
        },
    )
    assert called.status_code == 200
    limited = client.post(
        f"/api/v1/control/integrations/endpoints/{endpoint_id}/test/call",
        headers=headers,
        json={"agent_id": agent["id"], "tool_name": "remote.echo", "arguments": {}},
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1

    with _database().connection() as connection:
        audit_text = str(
            connection.execute(
                "SELECT action, metadata FROM veetee_audit_events "
                "WHERE action LIKE 'integration.%' ORDER BY created_at"
            ).fetchall()
        ).lower()
    assert "must-not-be-audited" not in audit_text
    assert "bearer" not in audit_text


def test_external_endpoints_are_tenant_scoped(integration_client: TestClient) -> None:
    client = integration_client
    owner = _login(client, "owner@example.test", "a-test-password-long-enough")
    endpoint = client.post(
        "/api/v1/control/integrations/endpoints",
        headers=owner,
        json={"name": "Tenant A", "url": "https://mcp.example.test/rpc"},
    ).json()
    other_email = "tools-other@example.test"
    other_password = "tools-other-password"
    with _database().connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'owner')",
            (uuid4(), other_email, hash_password(other_password)),
        )
    other = _login(client, other_email, other_password)
    assert client.get(
        f"/api/v1/control/integrations/endpoints/{endpoint['id']}", headers=other
    ).status_code == 404
    assert client.get("/api/v1/control/integrations/endpoints", headers=other).json() == []


def test_endpoint_host_must_be_in_runtime_allowlist(integration_client: TestClient) -> None:
    headers = _login(
        integration_client, "owner@example.test", "a-test-password-long-enough"
    )
    rejected = integration_client.post(
        "/api/v1/control/integrations/endpoints",
        headers=headers,
        json={"name": "Blocked", "url": "https://blocked.example.test/rpc"},
    )
    assert rejected.status_code == 422


def test_tool_integration_migration_is_idempotent_and_down_fails_closed() -> None:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _apply_migrations(database)
    _apply_migrations(database)
    owner_id = uuid4()
    endpoint_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'owner') ON CONFLICT (email) DO NOTHING",
            (owner_id, f"tool-migration-{owner_id}@example.test", hash_password("long-password")),
        )
        connection.execute(
            "INSERT INTO veetee_external_endpoints (id, owner_user_id, name, url) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, owner_id, f"endpoint-{endpoint_id}", "https://mcp.example.test/rpc"),
        )
        assert connection.execute(
            "SELECT count(*) FROM veetee_schema_migrations "
            "WHERE version = '009_m6_tool_integrations'"
        ).fetchone()[0] == 1
    with pytest.raises(psycopg.errors.RaiseException):
        with database.connection() as connection:
            connection.execute(
                (MIGRATIONS / "009_m6_tool_integrations.down.sql").read_text(
                    encoding="utf-8"
                )
            )
    with database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM veetee_external_endpoints WHERE id = %s", (endpoint_id,)
        ).fetchone()[0] == 1
        connection.execute("DELETE FROM veetee_external_endpoints WHERE id = %s", (endpoint_id,))
        connection.execute("DELETE FROM veetee_users WHERE id = %s", (owner_id,))
