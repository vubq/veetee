"""PostgreSQL-backed M6.8 administration and quota tests."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import (
    DatabaseConfig,
    PostgresDatabase,
    QuotaRepository,
    QuotaService,
    SystemSettingsRepository,
)
from veetee_server.persistence.repository import hash_password

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"


def _database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("M6.8 tests require an isolated veetee_test database")
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
        "010_m6_administration.sql",
        "011_m6_consent_transcript.sql",
    ):
        with database.connection() as connection:
            connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))


def _reset_database(database: PostgresDatabase) -> tuple[UUID, UUID]:
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_quota_usage_buckets, veetee_user_quotas, "
            "veetee_password_reset_tokens, veetee_system_settings, "
            "veetee_agent_integration_permissions, veetee_external_endpoints, "
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
        connection.execute(
            (MIGRATIONS / "010_m6_administration.sql").read_text(encoding="utf-8")
        )
        admin_id = uuid4()
        owner_id = uuid4()
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) VALUES "
            "(%s, 'admin@example.test', %s, 'admin'), "
            "(%s, 'owner@example.test', %s, 'owner')",
            (
                admin_id,
                hash_password("admin-password-long-enough"),
                owner_id,
                hash_password("owner-password-long-enough"),
            ),
        )
    return admin_id, owner_id


@pytest.fixture
def m68_state() -> tuple[TestClient, PostgresDatabase, UUID, UUID]:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _apply_migrations(database)
    admin_id, owner_id = _reset_database(database)
    app = create_app(
        Settings(
            environment="test",
            persistence_enabled=True,
            database_dsn=TEST_DATABASE_DSN,
            bootstrap_admin_email="admin@example.test",
            bootstrap_admin_password="admin-password-long-enough",
            ota_public_base_url="http://ota.example.test",
        )
    )
    with TestClient(app) as client:
        yield client, database, admin_id, owner_id


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/control/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_migration_is_idempotent_and_down_migration_fails_closed() -> None:
    database = _database()
    _apply_migrations(database)
    _reset_database(database)
    migration = (MIGRATIONS / "010_m6_administration.sql").read_text(encoding="utf-8")
    with database.connection() as connection:
        connection.execute(migration)
        assert connection.execute(
            "SELECT count(*) FROM veetee_schema_migrations "
            "WHERE version = '010_m6_administration'"
        ).fetchone()[0] == 1
        owner_id = connection.execute(
            "SELECT id FROM veetee_users WHERE email = 'owner@example.test'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO veetee_user_quotas (user_id, enabled) VALUES (%s, true)",
            (owner_id,),
        )
    with pytest.raises(Exception, match="contains data"):
        with database.connection() as connection:
            connection.execute(
                (MIGRATIONS / "010_m6_administration.down.sql").read_text(
                    encoding="utf-8"
                )
            )


def test_admin_rbac_create_reset_replay_and_redaction(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, database, _admin_id, _owner_id = m68_state
    owner_headers = _login(client, "owner@example.test", "owner-password-long-enough")
    admin_headers = _login(client, "admin@example.test", "admin-password-long-enough")
    assert client.get("/api/v1/control/admin/users", headers=owner_headers).status_code == 403

    created = client.post(
        "/api/v1/control/admin/users",
        headers=admin_headers,
        json={"email": "  New.User@Example.Test ", "role": "owner"},
    )
    assert created.status_code == 201
    body = created.json()
    user_id = UUID(body["user"]["id"])
    token = body["reset_token"]
    assert body["user"]["email"] == "new.user@example.test"
    assert "hash" not in str(body).lower()
    with database.connection() as connection:
        stored_hash = connection.execute(
            "SELECT token_hash FROM veetee_password_reset_tokens WHERE user_id = %s",
            (user_id,),
        ).fetchone()[0]
        audit_text = str(
            connection.execute(
                "SELECT metadata FROM veetee_audit_events WHERE resource_id = %s",
                (str(user_id),),
            ).fetchall()
        )
    assert stored_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in audit_text

    reset = client.post(
        "/api/v1/control/auth/reset-password",
        json={"token": token, "new_password": "replacement-password-long"},
    )
    assert reset.status_code == 200
    assert client.post(
        "/api/v1/control/auth/reset-password",
        json={"token": token, "new_password": "another-password-long"},
    ).status_code == 400
    assert _login(client, "new.user@example.test", "replacement-password-long")
    expired = client.post(
        f"/api/v1/control/admin/users/{user_id}/reset-token", headers=admin_headers
    )
    assert expired.status_code == 200
    expired_token = expired.json()["reset_token"]
    with database.connection() as connection:
        connection.execute(
            "UPDATE veetee_password_reset_tokens SET expires_at = now() - interval '1 second' "
            "WHERE token_hash = %s",
            (hashlib.sha256(expired_token.encode()).hexdigest(),),
        )
    assert client.post(
        "/api/v1/control/auth/reset-password",
        json={"token": expired_token, "new_password": "expired-password-long"},
    ).status_code == 400


def test_bootstrap_identity_is_admin_while_owner_remains_non_admin(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, _database_obj, _admin_id, _owner_id = m68_state
    admin_headers = _login(client, "admin@example.test", "admin-password-long-enough")
    owner_headers = _login(client, "owner@example.test", "owner-password-long-enough")
    assert client.get("/api/v1/control/admin/users", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/control/admin/users", headers=owner_headers).status_code == 403


def test_admin_user_guards_suspension_and_optimistic_conflict(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, _database_obj, admin_id, owner_id = m68_state
    admin_headers = _login(client, "admin@example.test", "admin-password-long-enough")
    owner_headers = _login(client, "owner@example.test", "owner-password-long-enough")
    assert client.put(
        f"/api/v1/control/admin/users/{admin_id}",
        headers=admin_headers,
        json={"expected_version": 1, "role": "owner"},
    ).status_code == 409
    assert client.put(
        f"/api/v1/control/admin/users/{owner_id}",
        headers=admin_headers,
        json={"expected_version": 99, "status": "suspended"},
    ).status_code == 409
    suspended = client.put(
        f"/api/v1/control/admin/users/{owner_id}",
        headers=admin_headers,
        json={"expected_version": 1, "status": "suspended"},
    )
    assert suspended.status_code == 200
    assert client.get("/api/v1/control/agents", headers=owner_headers).status_code == 401


def test_typed_settings_and_bounded_audit_search(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, _database_obj, _admin_id, _owner_id = m68_state
    headers = _login(client, "admin@example.test", "admin-password-long-enough")
    assert client.put(
        "/api/v1/control/admin/settings/arbitrary_secret",
        headers=headers,
        json={"expected_version": 1, "value": "secret"},
    ).status_code == 404
    assert client.put(
        "/api/v1/control/admin/settings/conversation_retention_days",
        headers=headers,
        json={"expected_version": 1, "value": True},
    ).status_code == 422
    assert client.put(
        "/api/v1/control/admin/settings/default_quota_llm_tokens_per_day",
        headers=headers,
        json={"expected_version": 1, "value": True},
    ).status_code == 422
    updated = client.put(
        "/api/v1/control/admin/settings/conversation_retention_days",
        headers=headers,
        json={"expected_version": 1, "value": 45},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.put(
        "/api/v1/control/admin/settings/conversation_retention_days",
        headers=headers,
        json={"expected_version": 1, "value": 30},
    ).status_code == 409
    audit = client.get(
        "/api/v1/control/admin/audit-logs",
        headers=headers,
        params={"action": "admin.setting", "limit": 1},
    )
    assert audit.status_code == 200
    assert audit.json()["items"][0]["action"] == "admin.setting.update"
    assert audit.json()["items"][0]["metadata"] == {"version": 2}
    assert client.get(
        "/api/v1/control/admin/audit-logs",
        headers=headers,
        params={
            "start_time": datetime.now(UTC).isoformat(),
            "end_time": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    ).status_code == 422


def test_rag_upload_quota_rejects_before_ingest(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, database, admin_id, owner_id = m68_state
    owner_headers = _login(client, "owner@example.test", "owner-password-long-enough")
    dataset = client.post(
        "/api/v1/control/knowledge/datasets",
        headers=owner_headers,
        json={"name": "Quota dataset"},
    )
    assert dataset.status_code == 201
    quota, error = QuotaRepository(database).update_user_quota(
        admin_id,
        owner_id,
        1,
        llm_tokens_per_day=None,
        tts_chars_per_day=None,
        tool_calls_per_minute=None,
        rag_bytes_per_month=5,
        enabled=True,
    )
    assert error is None and quota is not None
    uploaded = client.put(
        f"/api/v1/control/knowledge/datasets/{dataset.json()['id']}"
        "/documents/too-large.txt",
        headers={
            **owner_headers,
            "Content-Type": "text/plain",
        },
        content="123456",
    )
    assert uploaded.status_code == 429
    assert client.get(
        f"/api/v1/control/knowledge/datasets/{dataset.json()['id']}/documents",
        headers=owner_headers,
    ).json() == []


def test_quota_defaults_boundaries_windows_isolation_and_atomicity(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    _client, database, admin_id, owner_id = m68_state
    settings_repo = SystemSettingsRepository(database)
    quota_repo = QuotaRepository(database)
    service = QuotaService(database, settings_repo, quota_repo)

    assert service.check_and_consume(owner_id, "tool_calls_minute", 1).allowed
    with database.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM veetee_quota_usage_buckets"
        ).fetchone()[0] == 0

    policy, error = quota_repo.update_user_quota(
        admin_id,
        owner_id,
        1,
        llm_tokens_per_day=10,
        tts_chars_per_day=10,
        tool_calls_per_minute=1,
        rag_bytes_per_month=10,
        enabled=True,
    )
    assert error is None and policy is not None
    now = datetime(2026, 8, 22, 12, 0, 30, tzinfo=UTC)
    assert service.check_and_consume(owner_id, "tool_calls_minute", 1, now).allowed
    assert not service.check_and_consume(owner_id, "tool_calls_minute", 1, now).allowed
    assert service.check_and_consume(
        owner_id, "tool_calls_minute", 1, now + timedelta(minutes=1)
    ).allowed

    other_id = uuid4()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, 'quota-other@example.test', %s, 'owner')",
            (other_id, hash_password("quota-other-password")),
        )
    assert service.check_and_consume(other_id, "tool_calls_minute", 100, now).allowed

    next_minute = now + timedelta(minutes=2)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: service.check_and_consume(
                    owner_id, "tool_calls_minute", 1, next_minute
                ).allowed,
                range(8),
            )
        )
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_admin_quota_api_rejects_unknown_user_and_out_of_range_limit(
    m68_state: tuple[TestClient, PostgresDatabase, UUID, UUID],
) -> None:
    client, _database_obj, _admin_id, owner_id = m68_state
    headers = _login(client, "admin@example.test", "admin-password-long-enough")
    assert client.get(
        f"/api/v1/control/admin/quotas/{uuid4()}", headers=headers
    ).status_code == 404
    assert client.put(
        f"/api/v1/control/admin/quotas/{owner_id}",
        headers=headers,
        json={
            "expected_version": 1,
            "enabled": True,
            "tool_calls_per_minute": 2_147_483_648,
        },
    ).status_code == 422
