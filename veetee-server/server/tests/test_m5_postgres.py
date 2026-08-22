"""PostgreSQL integration tests for the secure M5 lifecycle vertical slice."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from psycopg.conninfo import conninfo_to_dict

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.domain.device_lifecycle import (
    canonical_activation_challenge,
    create_artifact_download_token,
)
from veetee_server.persistence import DatabaseConfig, PostgresDatabase
from veetee_server.persistence.device_repository import OtaRepository
from veetee_server.persistence.repository import hash_password

TEST_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")
ACTIVATION_SECRET = "activation-test-secret-32-characters"
DEVICE_SECRET = "device-token-test-secret-32-characters"


def _database() -> PostgresDatabase:
    parsed = conninfo_to_dict(TEST_DSN)
    if parsed.get("dbname") != "veetee_test":
        pytest.fail("M5 PostgreSQL tests require database name exactly veetee_test")
    database = PostgresDatabase(DatabaseConfig(TEST_DSN))
    try:
        database.check()
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL veetee_test is unavailable")
    return database


@pytest.fixture
def persisted_client(tmp_path: Path) -> TestClient:
    database = _database()
    migration = Path(__file__).parents[1] / "migrations/003_device_lifecycle_ota.sql"
    with database.connection() as connection:
        connection.execute(migration.read_text())
        connection.execute(
            "TRUNCATE veetee_ota_reports, veetee_ota_rollouts, veetee_ota_releases, "
            "veetee_ota_artifacts, veetee_device_binding_history, veetee_device_credentials, "
            "veetee_device_activation_challenges, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
    private_key = tmp_path / "test-ed25519.pem"
    public_der = tmp_path / "test-ed25519.der"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
        check=True,
    )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-outform",
            "DER",
            "-out",
            str(public_der),
        ],
        check=True,
    )
    settings = Settings(
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DSN,
        bootstrap_admin_email="owner-m5@example.test",
        bootstrap_admin_password="a-test-password-long-enough",
        activation_secret=ACTIVATION_SECRET,
        device_jwt_secret=DEVICE_SECRET,
        ota_artifact_dir=str(tmp_path),
        ota_public_base_url="https://ota.example.test",
        ota_discovery_min_interval_seconds=0,
        ota_ed25519_public_key=public_der.read_bytes()[-32:].hex(),
        allow_insecure_activation=True,
    )
    app = create_app(settings)
    app.state.test_private_key = str(private_key)
    with TestClient(app) as client:
        yield client


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner-m5@example.test", "password": "a-test-password-long-enough"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _owner_login(client: TestClient) -> tuple[object, dict[str, str]]:
    owner_id = uuid4()
    with _database().connection() as connection:
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'owner')",
            (owner_id, "owner-only@example.test", hash_password("owner-password-long-enough")),
        )
    response = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner-only@example.test", "password": "owner-password-long-enough"},
    )
    assert response.status_code == 200
    return owner_id, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _discover(client: TestClient, device_id: str = "device-m5") -> dict[str, object]:
    return _discover_response(client, device_id).json()


def _discover_response(
    client: TestClient,
    device_id: str = "device-m5",
    *,
    token: str = "",
    client_id: str = "client-m5",
    board: str = "board-a",
) -> Response:
    headers = {"Device-Id": device_id, "Client-Id": client_id}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.post(
        "/api/v1/devices/ota/check",
        headers=headers,
        json={
            "version": "1.0.0",
            "board": board,
            "chip_model_name": "esp32s3",
            "partition": "ota_0",
        },
    )
    return response


def _sign_activation(client: TestClient, device_id: str, client_id: str, nonce: str) -> str:
    message = Path(client.app.state.test_private_key).with_suffix(".challenge")
    signature = Path(client.app.state.test_private_key).with_suffix(".proof")
    message.write_bytes(canonical_activation_challenge(device_id, client_id, nonce))
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            client.app.state.test_private_key,
            "-rawin",
            "-in",
            str(message),
            "-out",
            str(signature),
        ],
        check=True,
    )
    return signature.read_bytes().hex()


def test_production_activation_requires_real_enrollment_proof(
    persisted_client: TestClient,
) -> None:
    settings = persisted_client.app.state.settings
    settings.allow_insecure_activation = False
    admin = _login(persisted_client)

    unknown = _discover_response(persisted_client, "unknown-device")
    assert unknown.status_code == 200
    assert unknown.json()["activation"] == {
        "status": "pending",
        "message": "Device enrollment proof is required.",
    }
    assert "code" not in unknown.text

    public_key = settings.ota_ed25519_public_key
    provisioned = persisted_client.post(
        "/api/v1/control/devices/provision",
        headers=admin,
        json={
            "device_id": "proved-device",
            "client_id": "proved-client",
            "ed25519_public_key": public_key,
            "board": "board-a",
            "chip": "esp32s3",
            "partition": "ota_0",
        },
    )
    assert provisioned.status_code == 201
    first = _discover_response(
        persisted_client, "proved-device", client_id="proved-client"
    ).json()
    assert "code" not in first["activation"]
    nonce = first["activation"]["nonce"]
    proof = _sign_activation(persisted_client, "proved-device", "proved-client", nonce)
    proved = persisted_client.post(
        "/api/v1/devices/ota/check",
        headers={
            "Device-Id": "proved-device",
            "Client-Id": "proved-client",
            "Activation-Nonce": nonce,
            "Activation-Proof": proof,
        },
        json={},
    )
    assert proved.status_code == 200
    assert len(proved.json()["activation"]["code"]) == 6
    replay = persisted_client.post(
        "/api/v1/devices/ota/check",
        headers={
            "Device-Id": "proved-device",
            "Client-Id": "proved-client",
            "Activation-Nonce": nonce,
            "Activation-Proof": proof,
        },
        json={},
    )
    assert replay.status_code == 401


def test_activation_bind_ws_rotation_unbind_and_audit(persisted_client: TestClient) -> None:
    first = _discover(persisted_client)
    assert first["websocket"]["token"] == ""
    code = first["activation"]["code"]
    bootstrap_token = first["activation"]["token"]
    polled = _discover(persisted_client)
    assert polled["activation"]["code"] == code
    assert polled["activation"]["challenge"] == first["activation"]["challenge"]
    assert "token" not in polled["activation"]
    headers = _login(persisted_client)
    wrong = persisted_client.post(
        "/api/v1/control/devices/bind",
        headers={**headers, "Idempotency-Key": "bind-wrong"},
        json={"device_id": "device-m5", "code": "999999"},
    )
    assert wrong.status_code == 400
    bound = persisted_client.post(
        "/api/v1/control/devices/bind",
        headers={**headers, "Idempotency-Key": "bind-device-m5"},
        json={"device_id": "device-m5", "code": code, "alias": "Bếp"},
    )
    assert bound.status_code == 200
    retry = persisted_client.post(
        "/api/v1/control/devices/bind",
        headers={**headers, "Idempotency-Key": "bind-device-m5"},
        json={"device_id": "device-m5", "code": code, "alias": "Bếp"},
    )
    assert retry.status_code == 200
    assert retry.json()["alias"] == "Bếp"
    assert (
        persisted_client.patch(
            "/api/v1/control/devices/device-m5",
            headers=headers,
            json={"alias": "Phòng khách"},
        ).status_code
        == 200
    )
    snapshot = persisted_client.post(
        "/api/v1/control/devices/bind",
        headers={**headers, "Idempotency-Key": "bind-device-m5"},
        json={"device_id": "device-m5", "code": code, "alias": "Bếp"},
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["alias"] == "Bếp"
    changed_request = persisted_client.post(
        "/api/v1/control/devices/bind",
        headers={**headers, "Idempotency-Key": "bind-device-m5"},
        json={"device_id": "device-m5", "code": code, "alias": "Khác"},
    )
    assert changed_request.status_code == 409
    takeover = _discover_response(
        persisted_client,
        client_id="attacker-client",
        token=bootstrap_token,
        board="attacker-board",
    )
    assert takeover.status_code == 401
    unauthenticated = _discover_response(persisted_client)
    assert unauthenticated.status_code == 401

    ws_headers = {
        "Protocol-Version": "1",
        "Device-Id": "device-m5",
        "Client-Id": "client-m5",
    }
    with persisted_client.websocket_connect(
        "/api/v1/devices/ws",
        headers={**ws_headers, "Authorization": f"Bearer {bootstrap_token}"},
    ) as websocket:
        assert websocket.receive_json()["code"] == "veetee_auth_failed"
    bootstrap_report = persisted_client.post(
        "/api/v1/devices/ota/report",
        headers={
            "Authorization": f"Bearer {bootstrap_token}",
            "Device-Id": "device-m5",
            "Client-Id": "client-m5",
        },
        json={"event_id": str(uuid4()), "stage": "check", "outcome": "success"},
    )
    assert bootstrap_report.status_code == 401

    token_one = _discover_response(persisted_client, token=bootstrap_token).json()["websocket"][
        "token"
    ]
    assert _discover_response(persisted_client, token=bootstrap_token).status_code == 401
    token_two = _discover_response(persisted_client, token=token_one).json()["websocket"]["token"]
    with persisted_client.websocket_connect(
        "/api/v1/devices/ws",
        headers={**ws_headers, "Authorization": f"Bearer {token_one}"},
    ) as websocket:
        assert websocket.receive_json()["code"] == "veetee_auth_failed"
    with persisted_client.websocket_connect(
        "/api/v1/devices/ws",
        headers={**ws_headers, "Authorization": f"Bearer {token_two}"},
    ) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            }
        )
        assert websocket.receive_json()["type"] == "hello"

    unbound = persisted_client.post(
        "/api/v1/control/devices/device-m5/unbind",
        headers={**headers, "Idempotency-Key": "unbind-device-m5"},
    )
    assert unbound.status_code == 200
    assert (
        persisted_client.post(
            "/api/v1/control/devices/device-m5/unbind",
            headers={**headers, "Idempotency-Key": "unbind-device-m5"},
        ).status_code
        == 200
    )
    with _database().connection() as connection:
        actions = [
            row[0]
            for row in connection.execute(
                "SELECT action FROM veetee_audit_events ORDER BY created_at"
            ).fetchall()
        ]
    assert actions.count("device.bind") == 1
    assert actions.count("device.unbind") == 1


def test_ota_admin_only_and_bootstrap_promotion_is_audited(
    persisted_client: TestClient,
) -> None:
    _, owner_headers = _owner_login(persisted_client)
    admin_headers = _login(persisted_client)
    owner_response = persisted_client.get("/api/v1/control/ota/releases", headers=owner_headers)
    admin_response = persisted_client.get("/api/v1/control/ota/releases", headers=admin_headers)
    assert owner_response.status_code == 403
    assert admin_response.status_code == 200
    with _database().connection() as connection:
        role = connection.execute(
            "SELECT role FROM veetee_users WHERE email = 'owner-m5@example.test'"
        ).fetchone()[0]
        audit = connection.execute(
            "SELECT 1 FROM veetee_audit_events WHERE action = 'identity.bootstrap_admin_created'"
        ).fetchone()
    assert role == "admin"
    assert audit is not None


def test_owner_recovery_issues_one_time_discovery_credential(
    persisted_client: TestClient,
) -> None:
    owner_id, owner_headers = _owner_login(persisted_client)
    with _database().connection() as connection:
        connection.execute(
            "INSERT INTO veetee_devices "
            "(id, owner_user_id, device_id, client_id, status) "
            "VALUES (%s, %s, 'recovery-device', '', 'recovery_required')",
            (uuid4(), owner_id),
        )
    admin_headers = _login(persisted_client)
    denied = persisted_client.post(
        "/api/v1/control/devices/recovery-device/recover",
        headers={"Authorization": "Bearer invalid"},
        json={"client_id": "recovered-client"},
    )
    assert denied.status_code == 401
    recovery = persisted_client.post(
        "/api/v1/control/devices/recovery-device/recover",
        headers=owner_headers,
        json={"client_id": "recovered-client"},
    )
    assert recovery.status_code == 200
    assert "websocket" not in recovery.json()
    recovery_token = recovery.json()["recovery_token"]
    rotated = _discover_response(
        persisted_client,
        "recovery-device",
        token=recovery_token,
        client_id="recovered-client",
    )
    assert rotated.status_code == 200
    ws_token = rotated.json()["websocket"]["token"]
    assert (
        _discover_response(
            persisted_client,
            "recovery-device",
            token=recovery_token,
            client_id="recovered-client",
        ).status_code
        == 401
    )
    with persisted_client.websocket_connect(
        "/api/v1/devices/ws",
        headers={
            "Authorization": f"Bearer {ws_token}",
            "Protocol-Version": "1",
            "Device-Id": "recovery-device",
            "Client-Id": "recovered-client",
        },
    ) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            }
        )
        assert websocket.receive_json()["type"] == "hello"
    assert persisted_client.get("/api/v1/control/devices", headers=admin_headers).status_code == 200


def test_rollout_download_range_and_report_idempotency(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    first = _discover(persisted_client, "device-ota")
    database = _database()
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT id FROM veetee_users WHERE email = %s", ("owner-m5@example.test",)
        ).fetchone()[0]
        connection.execute(
            "UPDATE veetee_devices SET owner_user_id = %s, status = 'bound' "
            "WHERE device_id = 'device-ota'",
            (owner,),
        )
    artifact_id = uuid4()
    content = b"0123456789abcdef"
    path = tmp_path / f"{artifact_id}.bin"
    path.write_bytes(content)
    repository = OtaRepository(database)
    artifact = repository.create_artifact(
        artifact_id,
        "board-a",
        "esp32s3",
        "ota_0",
        "firmware.bin",
        str(path),
        len(content),
        hashlib.sha256(content).hexdigest(),
        "1" * 128,
        provenance="range and report test",
    )
    release = repository.create_release(
        "1.1.0",
        artifact["id"],
        "board-a",
        "esp32s3",
        "ota_0",
        provenance="range and report test",
    )
    repository.publish_release(release["id"], cohort_percentage=100)

    discovery = _discover_response(
        persisted_client, "device-ota", token=first["activation"]["token"]
    ).json()
    download_url = discovery["firmware"]["url"]
    parsed = urlparse(download_url)
    token = parse_qs(parsed.query)["token"][0]
    partial = persisted_client.get(
        parsed.path, params={"token": token}, headers={"Range": "bytes=-4"}
    )
    assert partial.status_code == 206
    assert partial.content == b"cdef"
    assert (
        persisted_client.get(
            parsed.path, params={"token": token}, headers={"Range": "bytes=0-1,4-5"}
        ).status_code
        == 416
    )

    ws_token = discovery["websocket"]["token"]
    report_headers = {
        "Authorization": f"Bearer {ws_token}",
        "Device-Id": "device-ota",
        "Client-Id": "client-m5",
    }
    event_id = str(uuid4())
    report_base = {
        "release_id": str(release["id"]),
        "version": "1.1.0",
        "outcome": "success",
    }
    for stage in ("download", "install"):
        step = {**report_base, "event_id": str(uuid4()), "stage": stage}
        assert (
            persisted_client.post(
                "/api/v1/devices/ota/report", headers=report_headers, json=step
            ).status_code
            == 201
        )
    report = {**report_base, "event_id": event_id, "stage": "boot"}
    assert (
        persisted_client.post(
            "/api/v1/devices/ota/report", headers=report_headers, json=report
        ).status_code
        == 201
    )
    assert (
        persisted_client.post(
            "/api/v1/devices/ota/report", headers=report_headers, json=report
        ).status_code
        == 201
    )
    conflict = {**report, "outcome": "failure"}
    assert (
        persisted_client.post(
            "/api/v1/devices/ota/report", headers=report_headers, json=conflict
        ).status_code
        == 409
    )
    spam = {**conflict, "event_id": str(uuid4())}
    assert (
        persisted_client.post(
            "/api/v1/devices/ota/report", headers=report_headers, json=spam
        ).status_code
        == 409
    )
    rollout = next(
        item for item in repository.list_rollouts() if item["release_id"] == release["id"]
    )
    assert rollout["status"] == "active"


def test_down_migration_refuses_unbound_data() -> None:
    down = (Path(__file__).parents[1] / "migrations/003_device_lifecycle_ota.down.sql").read_text()
    assert "Cannot roll back M5 while M5 lifecycle or OTA data exists" in down
    assert "DELETE FROM veetee_devices WHERE owner_user_id IS NULL" not in down


def test_empty_down_then_up_and_m4_owner_recovery() -> None:
    database = _database()
    root = Path(__file__).parents[1] / "migrations"
    down = (root / "003_device_lifecycle_ota.down.sql").read_text()
    up = (root / "003_device_lifecycle_ota.sql").read_text()
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_ota_reports, veetee_ota_offers, veetee_ota_rollouts, "
            "veetee_ota_releases, veetee_ota_artifacts, veetee_idempotency_operations, "
            "veetee_device_binding_history, veetee_device_credentials, "
            "veetee_device_activation_challenges, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
        connection.execute(down)
        owner_id = uuid4()
        device_row_id = uuid4()
        connection.execute(
            "INSERT INTO veetee_users (id, email, password_hash, role) "
            "VALUES (%s, 'upgrade@example.test', 'unused', 'owner')",
            (owner_id,),
        )
        connection.execute(
            "INSERT INTO veetee_devices (id, owner_user_id, device_id) VALUES (%s, %s, %s)",
            (device_row_id, owner_id, "m4-device"),
        )
        connection.execute(up)
        upgraded = connection.execute(
            "SELECT owner_user_id, client_id, status FROM veetee_devices "
            "WHERE device_id = 'm4-device'"
        ).fetchone()
        assert upgraded == (owner_id, "", "recovery_required")


def test_upgrade_duplicate_device_id_fails_before_schema_change() -> None:
    database = _database()
    root = Path(__file__).parents[1] / "migrations"
    down = (root / "003_device_lifecycle_ota.down.sql").read_text()
    up = (root / "003_device_lifecycle_ota.sql").read_text()
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_ota_reports, veetee_ota_offers, veetee_ota_rollouts, "
            "veetee_ota_releases, veetee_ota_artifacts, veetee_idempotency_operations, "
            "veetee_device_binding_history, veetee_device_credentials, "
            "veetee_device_activation_challenges, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
        connection.execute(down)
        owners = (uuid4(), uuid4())
        for index, owner in enumerate(owners):
            connection.execute(
                "INSERT INTO veetee_users (id, email, password_hash, role) "
                "VALUES (%s, %s, 'unused', 'owner')",
                (owner, f"duplicate-{index}@example.test"),
            )
            connection.execute(
                "INSERT INTO veetee_devices (id, owner_user_id, device_id) VALUES (%s, %s, %s)",
                (uuid4(), owner, "duplicate-device"),
            )
        connection.commit()
    try:
        with database.connection() as failing_connection:
            with pytest.raises(
                psycopg.errors.RaiseException, match="duplicate device_id value.*resolved first"
            ):
                failing_connection.execute(up)
        with database.connection() as verification_connection:
            columns = {
                row[0]
                for row in verification_connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'veetee_devices'"
                ).fetchall()
            }
            assert "client_id" not in columns
            assert verification_connection.execute(
                "SELECT count(*) FROM veetee_devices WHERE device_id = 'duplicate-device'"
            ).fetchone()[0] == 2
    finally:
        with database.connection() as cleanup_connection:
            cleanup_connection.execute(
                "DELETE FROM veetee_devices WHERE device_id = 'duplicate-device'"
            )
            cleanup_connection.execute(
                "DELETE FROM veetee_users WHERE email LIKE 'duplicate-%@example.test'"
            )
            cleanup_connection.execute(up)


def test_down_migration_refuses_any_m5_row() -> None:
    database = _database()
    down = (Path(__file__).parents[1] / "migrations/003_device_lifecycle_ota.down.sql").read_text()
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_ota_reports, veetee_ota_offers, veetee_ota_rollouts, "
            "veetee_ota_releases, veetee_ota_artifacts, veetee_idempotency_operations, "
            "veetee_device_binding_history, veetee_device_credentials, "
            "veetee_device_activation_challenges, veetee_audit_events, veetee_memories, "
            "veetee_devices, veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
        connection.execute(
            "INSERT INTO veetee_ota_artifacts "
            "(id, board, chip, partition, file_name, file_path, file_size, sha256, provenance) "
            "VALUES (%s, 'b', 'c', 'p', 'f', '/tmp/f', 1, %s, 'test')",
            (uuid4(), "0" * 64),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.execute(down)


def test_signed_artifact_http_upload_range_and_corruption(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    first = _discover(persisted_client, "signed-device")
    control_headers = _login(persisted_client)
    assert (
        persisted_client.post(
            "/api/v1/control/devices/bind",
            headers={**control_headers, "Idempotency-Key": "bind-signed-device"},
            json={"device_id": "signed-device", "code": first["activation"]["code"]},
        ).status_code
        == 200
    )

    content = b"real signed firmware bytes\x00\x01"
    firmware = tmp_path / "signed-input.bin"
    signature = tmp_path / "signed-input.sig"
    firmware.write_bytes(content)
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-inkey",
            persisted_client.app.state.test_private_key,
            "-rawin",
            "-in",
            str(firmware),
            "-out",
            str(signature),
        ],
        check=True,
    )
    digest = hashlib.sha256(content).hexdigest()
    upload_headers = {
        **control_headers,
        "Content-Type": "application/octet-stream",
        "X-Artifact-SHA256": digest,
        "X-Artifact-Signature": signature.read_bytes().hex(),
        "X-Artifact-Name": "firmware.bin",
        "X-Artifact-Board": "board-a",
        "X-Artifact-Chip": "esp32s3",
        "X-Artifact-Partition": "ota_0",
        "X-Artifact-Provenance": "pytest generated signed fixture",
    }
    bad_hash = persisted_client.post(
        "/api/v1/control/ota/artifacts",
        headers={**upload_headers, "X-Artifact-SHA256": "0" * 64},
        content=content,
    )
    assert bad_hash.status_code == 400
    bad_signature = persisted_client.post(
        "/api/v1/control/ota/artifacts",
        headers={**upload_headers, "X-Artifact-Signature": "0" * 128},
        content=content,
    )
    assert bad_signature.status_code == 400
    uploaded = persisted_client.post(
        "/api/v1/control/ota/artifacts", headers=upload_headers, content=content
    )
    assert uploaded.status_code == 201
    artifact = uploaded.json()

    release = persisted_client.post(
        "/api/v1/control/ota/releases",
        headers=control_headers,
        json={
            "version": "1.2.0",
            "artifact_id": artifact["id"],
            "board": "board-a",
            "chip": "esp32s3",
            "partition": "ota_0",
            "provenance": "pytest openssl generated artifact",
        },
    )
    assert release.status_code == 201
    assert (
        persisted_client.post(
            f"/api/v1/control/ota/releases/{release.json()['id']}/publish",
            headers=control_headers,
        ).status_code
        == 200
    )

    discovery = _discover_response(
        persisted_client,
        "signed-device",
        token=first["activation"]["token"],
    ).json()
    assert discovery["firmware"]["sha256"] == digest
    assert discovery["firmware"]["signature_algorithm"] == "ed25519"
    parsed = urlparse(discovery["firmware"]["url"])
    token = parse_qs(parsed.query)["token"][0]
    ranged = persisted_client.get(
        parsed.path, params={"token": token}, headers={"Range": "bytes=5-10"}
    )
    assert ranged.status_code == 206
    assert ranged.content == content[5:11]

    stored_path = Path(artifact["file_path"])
    stored_path.write_bytes(b"X" + content[1:])
    assert stored_path.stat().st_size == len(content)
    assert persisted_client.get(parsed.path, params={"token": token}).status_code == 404

    with _database().connection() as connection:
        connection.execute(
            "ALTER TABLE veetee_ota_artifacts DISABLE TRIGGER veetee_ota_artifacts_immutable"
        )
        connection.execute(
            "UPDATE veetee_ota_artifacts SET file_path = '/tmp/outside.bin' WHERE id = %s",
            (artifact["id"],),
        )
        connection.execute(
            "ALTER TABLE veetee_ota_artifacts ENABLE TRIGGER veetee_ota_artifacts_immutable"
        )
    assert persisted_client.get(parsed.path, params={"token": token}).status_code == 404


def test_semver_selection_and_auto_update_gate(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    first = _discover(persisted_client, "semver-device")
    database = _database()
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT id FROM veetee_users WHERE email = %s", ("owner-m5@example.test",)
        ).fetchone()[0]
        connection.execute(
            "UPDATE veetee_devices SET owner_user_id = %s, status = 'bound' "
            "WHERE device_id = 'semver-device'",
            (owner,),
        )
    repository = OtaRepository(database)
    for version in ("1.0.0-alpha.10", "1.0.0-alpha.2"):
        content = version.encode()
        artifact_id = uuid4()
        path = tmp_path / f"{artifact_id}.bin"
        path.write_bytes(content)
        artifact = repository.create_artifact(
            artifact_id,
            "board-a",
            "esp32s3",
            "ota_0",
            path.name,
            str(path),
            len(content),
            hashlib.sha256(content).hexdigest(),
            "1" * 128,
            provenance="semantic version test",
        )
        release = repository.create_release(
            version, artifact["id"], "board-a", "esp32s3", "ota_0", provenance="test"
        )
        repository.publish_release(release["id"])
    discovery = _discover_response(
        persisted_client,
        "semver-device",
        token=first["activation"]["token"],
    ).json()
    assert discovery["firmware"]["version"] == "1.0.0-alpha.10"
    with database.connection() as connection:
        connection.execute(
            "UPDATE veetee_devices SET auto_update = false WHERE device_id = 'semver-device'"
        )
    blocked = _discover_response(
        persisted_client,
        "semver-device",
        token=discovery["websocket"]["token"],
    ).json()
    assert blocked["firmware"] == {"version": "", "url": ""}


def test_report_limits_and_host_header_cannot_change_artifact_url(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    first = _discover(persisted_client, "bounded-report-device")
    admin_headers = _login(persisted_client)
    assert (
        persisted_client.post(
            "/api/v1/control/devices/bind",
            headers={**admin_headers, "Idempotency-Key": "bind-bounded-report"},
            json={
                "device_id": "bounded-report-device",
                "code": first["activation"]["code"],
            },
        ).status_code
        == 200
    )
    ws_token = _discover_response(
        persisted_client, "bounded-report-device", token=first["activation"]["token"]
    ).json()["websocket"]["token"]
    report_headers = {
        "Authorization": f"Bearer {ws_token}",
        "Device-Id": "bounded-report-device",
        "Client-Id": "client-m5",
        "Content-Type": "application/json",
    }
    oversized = persisted_client.post(
        "/api/v1/devices/ota/report",
        headers=report_headers,
        content=b"{" + b" " * 20000 + b"}",
    )
    assert oversized.status_code == 413
    deep: object = "leaf"
    for index in range(8):
        deep = {f"level{index}": deep}
    too_deep = persisted_client.post(
        "/api/v1/devices/ota/report",
        headers=report_headers,
        json={
            "event_id": str(uuid4()),
            "stage": "check",
            "outcome": "success",
            "metadata": deep,
        },
    )
    assert too_deep.status_code == 422

    database = _database()
    content = b"host-safe"
    artifact_id = uuid4()
    path = tmp_path / f"{artifact_id}.bin"
    path.write_bytes(content)
    repository = OtaRepository(database)
    artifact = repository.create_artifact(
        artifact_id,
        "board-a",
        "esp32s3",
        "ota_0",
        path.name,
        str(path),
        len(content),
        hashlib.sha256(content).hexdigest(),
        "1" * 128,
        provenance="host injection test",
    )
    release = repository.create_release(
        "1.1.0",
        artifact["id"],
        "board-a",
        "esp32s3",
        "ota_0",
        provenance="host injection test",
    )
    repository.publish_release(release["id"])
    discovery = persisted_client.post(
        "/api/v1/devices/ota/check",
        headers={
            "Authorization": f"Bearer {ws_token}",
            "Device-Id": "bounded-report-device",
            "Client-Id": "client-m5",
            "Host": "attacker.example",
        },
        json={
            "version": "1.0.0",
            "board": "board-a",
            "chip_model_name": "esp32s3",
            "partition": "ota_0",
        },
    )
    assert discovery.status_code == 200
    assert discovery.json()["firmware"]["url"].startswith("https://ota.example.test/")
    assert "attacker.example" not in discovery.json()["firmware"]["url"]


def test_rollback_target_validation_and_activation(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    repository = OtaRepository(_database())

    def artifact(version: str, board: str = "board-a") -> dict[str, object]:
        content = version.encode()
        artifact_id = uuid4()
        path = tmp_path / f"{artifact_id}.bin"
        path.write_bytes(content)
        return repository.create_artifact(
            artifact_id,
            board,
            "esp32s3",
            "ota_0",
            path.name,
            str(path),
            len(content),
            hashlib.sha256(content).hexdigest(),
            "1" * 128,
            provenance=f"rollback fixture {version}",
        )

    lower_artifact = artifact("1.0.0")
    lower = repository.create_release(
        "1.0.0",
        lower_artifact["id"],
        "board-a",
        "esp32s3",
        "ota_0",
        provenance="published lower target",
    )
    repository.publish_release(lower["id"])
    unpublished_artifact = artifact("1.1.0")
    unpublished = repository.create_release(
        "1.1.0",
        unpublished_artifact["id"],
        "board-a",
        "esp32s3",
        "ota_0",
        provenance="unpublished target",
    )
    current_artifact = artifact("2.0.0")
    with pytest.raises(ValueError, match="published"):
        repository.create_release(
            "2.0.0",
            current_artifact["id"],
            "board-a",
            "esp32s3",
            "ota_0",
            provenance="invalid unpublished rollback",
            rollback_target_id=unpublished["id"],
        )
    incompatible_artifact = artifact("0.9.0", board="board-b")
    incompatible = repository.create_release(
        "0.9.0",
        incompatible_artifact["id"],
        "board-b",
        "esp32s3",
        "ota_0",
        provenance="incompatible target",
    )
    repository.publish_release(incompatible["id"])
    with pytest.raises(ValueError, match="match"):
        repository.create_release(
            "2.0.0",
            current_artifact["id"],
            "board-a",
            "esp32s3",
            "ota_0",
            provenance="invalid incompatible rollback",
            rollback_target_id=incompatible["id"],
        )
    valid = repository.create_release(
        "2.0.0",
        current_artifact["id"],
        "board-a",
        "esp32s3",
        "ota_0",
        provenance="valid rollback source",
        rollback_target_id=lower["id"],
    )
    repository.publish_release(valid["id"])
    source_rollout = next(
        rollout for rollout in repository.list_rollouts() if rollout["release_id"] == valid["id"]
    )
    response = persisted_client.post(
        f"/api/v1/control/ota/rollouts/{source_rollout['id']}/rollback",
        headers=_login(persisted_client),
        json={"scope": "rollout"},
    )
    assert response.status_code == 200
    assert response.json()["release_id"] == str(lower["id"])
    assert response.json()["status"] == "active"

    database = _database()
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT id FROM veetee_users WHERE email = 'owner-m5@example.test'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO veetee_devices "
            "(id, owner_user_id, device_id, client_id, status, board, chip, partition, "
            "current_firmware_version) VALUES (%s, %s, 'rollback-device', 'rollback-client', "
            "'bound', 'board-a', 'esp32s3', 'ota_0', '2.0.0')",
            (uuid4(), owner),
        )
    offered = repository.get_eligible_release(
        "rollback-device", "board-a", "esp32s3", "ota_0", "2.0.0"
    )
    assert offered is not None
    rollback_release, rollback_artifact = offered
    assert rollback_release["id"] == lower["id"]
    token = create_artifact_download_token(
        rollback_artifact["id"], "rollback-device", 60, DEVICE_SECRET
    )
    downloaded = persisted_client.get(
        f"/api/v1/devices/ota/artifacts/{rollback_artifact['id']}",
        params={"token": token},
        headers={"Range": "bytes=0-"},
    )
    assert downloaded.status_code == 206
    assert downloaded.content == b"1.0.0"
    for stage in ("download", "install", "boot"):
        repository.record_report(
            uuid4(),
            "rollback-device",
            lower["id"],
            "1.0.0",
            stage,
            "success",
        )
    with database.connection() as connection:
        assert connection.execute(
            "SELECT current_firmware_version FROM veetee_devices "
            "WHERE device_id = 'rollback-device'"
        ).fetchone()[0] == "1.0.0"


def test_device_scoped_rollback_keeps_source_and_blocks_outside_scope_below_target(
    persisted_client: TestClient, tmp_path: Path
) -> None:
    repository = OtaRepository(_database())

    def release(version: str, rollback_target_id: object | None = None) -> dict[str, object]:
        content = f"scoped-{version}".encode()
        artifact_id = uuid4()
        path = tmp_path / f"{artifact_id}.bin"
        path.write_bytes(content)
        artifact = repository.create_artifact(
            artifact_id,
            "board-a",
            "esp32s3",
            "ota_0",
            path.name,
            str(path),
            len(content),
            hashlib.sha256(content).hexdigest(),
            "1" * 128,
            provenance=f"scoped rollback fixture {version}",
        )
        created = repository.create_release(
            version,
            artifact["id"],
            "board-a",
            "esp32s3",
            "ota_0",
            provenance=f"scoped rollback release {version}",
            rollback_target_id=rollback_target_id,
        )
        repository.publish_release(created["id"])
        return created

    target = release("1.0.0")
    source = release("2.0.0", target["id"])
    source_rollout = next(
        rollout for rollout in repository.list_rollouts() if rollout["release_id"] == source["id"]
    )
    database = _database()
    with database.connection() as connection:
        owner = connection.execute(
            "SELECT id FROM veetee_users WHERE email = 'owner-m5@example.test'"
        ).fetchone()[0]
        for device_id, current_version in (
            ("scoped-rollback-device", "2.0.0"),
            ("outside-rollback-device", "0.5.0"),
        ):
            connection.execute(
                "INSERT INTO veetee_devices "
                "(id, owner_user_id, device_id, client_id, status, board, chip, partition, "
                "current_firmware_version) VALUES (%s, %s, %s, %s, 'bound', "
                "'board-a', 'esp32s3', 'ota_0', %s)",
                (uuid4(), owner, device_id, f"{device_id}-client", current_version),
            )

    response = persisted_client.post(
        f"/api/v1/control/ota/rollouts/{source_rollout['id']}/rollback",
        headers=_login(persisted_client),
        json={"scope": "device", "device_id": "scoped-rollback-device"},
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "rollback"
    assert response.json()["rollback_scope"] == "device"
    assert response.json()["rollback_device_id"] == "scoped-rollback-device"

    source_after = next(
        rollout
        for rollout in repository.list_rollouts()
        if rollout["id"] == source_rollout["id"]
    )
    assert source_after["status"] == "active"

    scoped = repository.get_eligible_release(
        "scoped-rollback-device", "board-a", "esp32s3", "ota_0", "2.0.0"
    )
    assert scoped is not None
    assert scoped[0]["id"] == target["id"]

    outside = repository.get_eligible_release(
        "outside-rollback-device", "board-a", "esp32s3", "ota_0", "0.5.0"
    )
    assert outside is not None
    assert outside[0]["id"] == source["id"]
    assert outside[0]["id"] != target["id"]
