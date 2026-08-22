"""M4/M5 audit fix: PostgreSQL-backed login throttling integration tests."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
EMAIL = "owner@example.test"
PASSWORD = "a-test-password-long-enough"


def _database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("Login throttle tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


@pytest.fixture
def limited_client() -> TestClient:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    with database.connection() as connection:
        connection.execute((MIGRATIONS / "004_login_rate_limit.sql").read_text(encoding="utf-8"))
        connection.execute(
            "TRUNCATE veetee_login_attempts, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
    settings = Settings(
        app_name="test-login-throttle",
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DATABASE_DSN,
        ota_public_base_url="http://ota.example.test",
        bootstrap_admin_email=EMAIL,
        bootstrap_admin_password=PASSWORD,
        login_rate_limit=3,
        login_rate_window_seconds=600,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _login(client: TestClient, email: str, password: str):
    return client.post("/api/v1/control/auth/login", json={"email": email, "password": password})


def _login_audit_actions() -> list[tuple[str, str, str]]:
    with _database().connection() as connection:
        rows = connection.execute(
            "SELECT action, resource_id, metadata::text FROM veetee_audit_events "
            "WHERE action LIKE 'auth.login%' ORDER BY created_at"
        ).fetchall()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def test_migration_004_forward_and_down_are_safe() -> None:
    up = (MIGRATIONS / "004_login_rate_limit.sql").read_text(encoding="utf-8")
    down = (MIGRATIONS / "004_login_rate_limit.down.sql").read_text(encoding="utf-8")
    assert "BEGIN;" in up and "COMMIT;" in down
    assert "CREATE TABLE IF NOT EXISTS veetee_login_attempts" in up
    assert "email_hash TEXT NOT NULL CHECK" in up
    assert "veetee_login_attempts_scope_idx" in up
    assert "INSERT INTO veetee_schema_migrations(version)" in up
    assert "'004_login_rate_limit'" in up and "'004_login_rate_limit'" in down
    assert "DROP TABLE IF EXISTS veetee_login_attempts" in down
    # The table only holds throttling counters, so rollback never refuses.
    assert "RAISE EXCEPTION" not in down
    assert "TRUNCATE" not in up and "TRUNCATE" not in down


def test_migration_004_round_trip_restores_state() -> None:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    up = (MIGRATIONS / "004_login_rate_limit.sql").read_text(encoding="utf-8")
    down = (MIGRATIONS / "004_login_rate_limit.down.sql").read_text(encoding="utf-8")
    with database.connection() as connection:
        connection.execute(down)
        assert connection.execute(
            "SELECT to_regclass('veetee_login_attempts')"
        ).fetchone()[0] is None
        connection.execute(up)
        assert connection.execute(
            "SELECT count(*) FROM veetee_schema_migrations WHERE version = '004_login_rate_limit'"
        ).fetchone()[0] == 1


def test_login_success_records_redacted_audit_and_keeps_working(
    limited_client: TestClient,
) -> None:
    for _ in range(5):  # successes must never consume quota
        response = _login(limited_client, EMAIL, PASSWORD)
        assert response.status_code == 200
        token = response.json()["access_token"]
        assert limited_client.get(
            "/api/v1/control/agents", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 200

    events = _login_audit_actions()
    assert [event[0] for event in events] == ["auth.login.success"] * 5
    with _database().connection() as connection:
        failures = connection.execute(
            "SELECT count(*) FROM veetee_login_attempts WHERE attempted_at > now()"
        ).fetchone()[0]
    assert failures == 0


def test_login_failure_is_audited_without_identifier_disclosure(
    limited_client: TestClient,
) -> None:
    wrong = _login(limited_client, EMAIL, "wrong-password-long-enough")
    unknown = _login(limited_client, "nobody@example.test", "wrong-password-long-enough")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == {"detail": "Invalid credentials"}
    assert unknown.json() == {"detail": "Invalid credentials"}

    events = _login_audit_actions()
    assert [event[0] for event in events] == ["auth.login.failure", "auth.login.failure"]
    for _, resource_id, metadata in events:
        assert len(resource_id) == 64 and all(c in "0123456789abcdef" for c in resource_id)
        assert metadata == "{}"
    assert "@" not in repr(events)


def test_login_quota_persists_returns_429_with_retry_after(
    limited_client: TestClient,
) -> None:
    for _ in range(3):
        response = _login(limited_client, EMAIL, "wrong-password-long-enough")
        assert response.status_code == 401

    blocked_valid = _login(limited_client, EMAIL, PASSWORD)
    assert blocked_valid.status_code == 429
    retry_after = int(blocked_valid.headers["Retry-After"])
    assert 1 <= retry_after <= 600

    # An address that cannot exist but has the same failure history must be
    # treated identically: responses never reveal whether the account exists.
    for _ in range(3):
        warmup = _login(limited_client, "ghost@example.test", "wrong-password-long-enough")
        assert warmup.status_code == 401
    blocked_unknown = _login(
        limited_client, "ghost@example.test", "wrong-password-long-enough"
    )
    assert blocked_unknown.status_code == 429
    assert blocked_unknown.json() == {"detail": "Too many login attempts"}
    assert 1 <= int(blocked_unknown.headers["Retry-After"]) <= 600

    events = _login_audit_actions()
    rate_limited = [event for event in events if event[0] == "auth.login.rate_limited"]
    assert len(rate_limited) == 2
    for _, resource_id, metadata in rate_limited:
        assert "@" not in resource_id and metadata == "{}"

    with _database().connection() as connection:
        sessions = connection.execute("SELECT count(*) FROM veetee_sessions").fetchone()[0]
        attempts = connection.execute("SELECT count(*) FROM veetee_login_attempts").fetchone()[0]
    assert sessions == 0  # no session may leak through a throttled attempt
    assert attempts == 6  # only real failed verifications consume quota
