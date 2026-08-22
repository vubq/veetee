"""Firmware-style M5 lifecycle tests backed by the isolated PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase
from veetee_server.persistence.repository import UserRepository

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")


def _database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("M5 tests require the isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


@pytest.fixture
def m5_client(tmp_path: Path) -> TestClient:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    with database.connection() as connection:
        connection.execute(
            "TRUNCATE veetee_device_bind_receipts, veetee_device_bind_attempts, "
            "veetee_device_activations, "
            "veetee_firmware_releases, veetee_firmware_artifacts, veetee_audit_events, "
            "veetee_memories, veetee_conversations, veetee_devices, veetee_provider_configs, "
            "veetee_agents, veetee_sessions, veetee_users CASCADE"
        )
    settings = Settings(
        app_name="test-m5",
        environment="test",
        persistence_enabled=True,
        database_dsn=TEST_DATABASE_DSN,
        bootstrap_admin_email="owner@example.test",
        bootstrap_admin_password="a-test-password-long-enough",
        activation_console_url="http://console.example.test/devices",
        ota_artifact_dir=str(tmp_path),
        ota_public_base_url="http://ota.example.test",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _auth(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/control/auth/login",
        json={"email": "owner@example.test", "password": "a-test-password-long-enough"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _agent(client: TestClient, headers: dict[str, str], name: str = "M5 agent") -> str:
    response = client.post("/api/v1/control/agents", headers=headers, json={"name": name})
    assert response.status_code == 201
    return str(response.json()["id"])


def _device_headers(suffix: str = "one") -> dict[str, str]:
    return {"Device-Id": f"device-{suffix}", "Client-Id": f"client-{suffix}"}


def _system_info(version: str = "1.0.0") -> dict[str, Any]:
    return {
        "application": {"name": "Veetee", "version": version},
        "board": {"type": "bread-compact-wifi-lcd", "name": "Bread Compact"},
        "ota": {"label": "app"},
        "chip_model_name": "esp32s3",
    }


def test_full_activation_bind_unbind_rebind_lifecycle(m5_client: TestClient) -> None:
    auth = _auth(m5_client)
    agent_id = _agent(m5_client, auth)
    device_headers = _device_headers()

    first = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    )
    assert first.status_code == 200
    activation = first.json()["activation"]
    assert len(activation["code"]) == 6 and activation["code"].isdigit()
    assert activation["challenge"]
    assert 0 < activation["timeout_ms"] <= 600_000
    assert activation["message"].splitlines() == [
        "http://console.example.test/devices",
        activation["code"],
    ]

    repeated = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]
    assert repeated["code"] == activation["code"]
    assert repeated["challenge"] == activation["challenge"]

    pending = m5_client.post(
        "/api/v1/devices/ota/check/activate", headers=device_headers, json={}
    )
    assert pending.status_code == 202
    assert pending.json() == {"activated": False}
    assert m5_client.post(
        "/api/v1/devices/ota/check/activate", headers=device_headers, json={"extra": True}
    ).status_code == 400

    bound = m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": agent_id, "code": activation["code"]},
    )
    assert bound.status_code == 200
    device_pk = bound.json()["id"]
    assert bound.json()["board"] == "bread-compact-wifi-lcd"
    assert bound.json()["chip"] == "esp32s3"
    assert bound.json()["partition"] == "app"
    assert bound.json()["version"] == "1.0.0"

    retry = m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": agent_id, "code": activation["code"]},
    )
    assert retry.status_code == 200
    assert retry.json()["id"] == device_pk
    for _ in range(25):
        assert m5_client.post(
            "/api/v1/control/devices/bind",
            headers=auth,
            json={"agent_id": agent_id, "code": activation["code"]},
        ).status_code == 200
    assert m5_client.post(
        "/api/v1/devices/ota/check/activate", headers=device_headers, json={}
    ).status_code == 200

    bound_check = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    )
    assert "activation" not in bound_check.json()
    assert bound_check.json()["websocket"]["url"].endswith("/api/v1/devices/ws")
    checked_device = m5_client.get("/api/v1/control/devices", headers=auth).json()[0]
    assert checked_device["last_seen_at"] is not None
    identity_conflict = m5_client.post(
        "/api/v1/devices/ota/check",
        headers={**device_headers, "Client-Id": "different-client"},
        json=_system_info(),
    )
    assert identity_conflict.status_code == 409
    listing = m5_client.get("/api/v1/control/devices", headers=auth)
    assert listing.status_code == 200
    assert listing.json()[0]["online"] is False

    websocket_headers = {
        "Authorization": "Bearer test-gateway-token",
        "Protocol-Version": "1",
        **device_headers,
    }
    m5_client.app.state.settings.device_gateway_token = "test-gateway-token"
    with m5_client.websocket_connect(
        "/api/v1/devices/ws", headers=websocket_headers
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
        assert m5_client.get("/api/v1/control/devices", headers=auth).json()[0]["online"]

    other_users = UserRepository(_database())
    other_users.ensure_bootstrap("other@example.test", "another-password-long-enough")
    other_login = m5_client.post(
        "/api/v1/control/auth/login",
        json={"email": "other@example.test", "password": "another-password-long-enough"},
    )
    other_auth = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    other_agent_id = _agent(m5_client, other_auth, "Other tenant agent")
    assert m5_client.get("/api/v1/control/devices", headers=other_auth).json() == []
    assert m5_client.delete(
        f"/api/v1/control/devices/{device_pk}", headers=other_auth
    ).status_code == 404
    assert m5_client.post(
        "/api/v1/control/devices/bind",
        headers=other_auth,
        json={"agent_id": other_agent_id, "code": activation["code"]},
    ).status_code == 409

    assert m5_client.delete(
        f"/api/v1/control/devices/{device_pk}", headers=auth
    ).status_code == 204
    fresh = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]
    assert fresh["code"] != activation["code"]
    assert fresh["challenge"] != activation["challenge"]


def test_expiry_ownership_collision_replay_race_and_quota(m5_client: TestClient) -> None:
    auth = _auth(m5_client)
    agent_id = _agent(m5_client, auth)
    other_agent = _agent(m5_client, auth, "Other M5 agent")
    device_headers = _device_headers("race")
    activation = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]

    with _database().connection() as connection:
        connection.execute(
            "UPDATE veetee_device_activations SET expires_at = now() - interval '1 second' "
            "WHERE code = %s",
            (activation["code"],),
        )
    expired = m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": agent_id, "code": activation["code"]},
    )
    assert expired.status_code == 410

    replacement = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]
    repository = m5_client.app.state.activation_repository
    owner_id = m5_client.app.state.user_repository.resolve_token(auth["Authorization"][7:])
    assert owner_id is not None
    from uuid import UUID

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(repository.bind_device, owner_id, UUID(agent), replacement["code"])
            for agent in (agent_id, other_agent)
        ]
        results = [future.result() for future in futures]
    assert sum(device is not None for device, _ in results) == 1
    assert sum(error == "already_bound_conflict" for _, error in results) == 1

    loser_agent = other_agent if results[0][0] is not None else agent_id
    replay = m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": loser_agent, "code": replacement["code"]},
    )
    assert replay.status_code == 409

    invalid_statuses = [
        m5_client.post(
            "/api/v1/control/devices/bind",
            headers=auth,
            json={"agent_id": agent_id, "code": f"{index:06d}"},
        ).status_code
        for index in range(25)
    ]
    assert 429 in invalid_statuses


def test_expired_bind_receipt_allows_code_reuse(
    m5_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth(m5_client)
    agent_id = _agent(m5_client, auth)
    repository = m5_client.app.state.activation_repository
    owner_id = m5_client.app.state.user_repository.resolve_token(auth["Authorization"][7:])
    assert owner_id is not None
    from uuid import UUID

    monkeypatch.setattr(
        "veetee_server.persistence.repository.secrets.randbelow", lambda _limit: 123456
    )
    first = repository.get_or_create(
        "receipt-device-one", "receipt-client-one", "board", "chip", "app", "1.0.0"
    )
    device, error = repository.bind_device(
        owner_id, UUID(agent_id), first.code, receipt_ttl_seconds=600
    )
    assert error is None and device is not None

    with _database().connection() as connection:
        connection.execute(
            "UPDATE veetee_device_bind_receipts SET expires_at = now() - interval '1 second'"
        )
    second = repository.get_or_create(
        "receipt-device-two", "receipt-client-two", "board", "chip", "app", "1.0.0"
    )
    assert second.code == first.code


def test_persistence_requires_explicit_safe_ota_public_url() -> None:
    with pytest.raises(ValidationError, match="ota_public_base_url"):
        Settings(environment="test", persistence_enabled=True)
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            persistence_enabled=True,
            ota_public_base_url="http://ota.example.test/base?redirect=unsafe",
        )


def test_upload_publish_download_and_firmware_eligibility(m5_client: TestClient) -> None:
    auth = _auth(m5_client)
    agent_id = _agent(m5_client, auth)
    device_headers = _device_headers("ota")
    activation = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]
    assert m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": agent_id, "code": activation["code"]},
    ).status_code == 200

    firmware = b"immutable-firmware-test-payload"
    upload = m5_client.post(
        "/api/v1/control/ota/artifacts",
        headers={**auth, "Content-Type": "application/octet-stream"},
        content=firmware,
    )
    assert upload.status_code == 201
    assert upload.json()["size"] == len(firmware)
    assert len(upload.json()["sha256"]) == 64

    release = m5_client.post(
        "/api/v1/control/ota/releases",
        headers=auth,
        json={
            "artifact_id": upload.json()["id"],
            "version": "2.0.0",
            "board": "bread-compact-wifi-lcd",
            "chip": "esp32s3",
            "partition": "app",
            "force": False,
        },
    )
    assert release.status_code == 201
    before_publish = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()
    assert before_publish["firmware"] == {"version": "", "url": ""}
    assert m5_client.post(
        f"/api/v1/control/ota/releases/{release.json()['id']}/publish", headers=auth
    ).status_code == 200

    eligible = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["firmware"]
    assert eligible["version"] == "2.0.0"
    assert eligible["force"] == 0
    assert type(eligible["force"]) is int
    host_injection_check = m5_client.post(
        "/api/v1/devices/ota/check",
        headers={**device_headers, "Host": "attacker.invalid"},
        json=_system_info(),
    ).json()["firmware"]
    assert host_injection_check["url"] == eligible["url"]
    download_path = eligible["url"].removeprefix("http://ota.example.test")
    downloaded = m5_client.get(download_path)
    assert downloaded.status_code == 200
    assert downloaded.content == firmware
    assert downloaded.headers["cache-control"] == "public, immutable"

    artifact_root = Path(m5_client.app.state.settings.ota_artifact_dir)
    artifact_files = list(artifact_root.iterdir())
    assert len(artifact_files) == 1
    artifact_files[0].chmod(0o600)
    artifact_files[0].write_bytes(b"X" * len(firmware))
    assert m5_client.get(download_path).status_code == 409

    current = m5_client.post(
        "/api/v1/devices/ota/check",
        headers=device_headers,
        json=_system_info("2.0.0"),
    ).json()
    assert current["firmware"] == {"version": "", "url": ""}
    equivalent = m5_client.post(
        "/api/v1/devices/ota/check",
        headers=device_headers,
        json=_system_info("2.0"),
    ).json()
    assert equivalent["firmware"] == {"version": "", "url": ""}
    incompatible = m5_client.post(
        "/api/v1/devices/ota/check",
        headers=device_headers,
        json={**_system_info(), "board": {"type": "different-board"}},
    ).json()
    assert incompatible["firmware"] == {"version": "", "url": ""}


def test_release_eligibility_is_tenant_scoped(m5_client: TestClient) -> None:
    owner_a = _auth(m5_client)
    agent_a = _agent(m5_client, owner_a)
    headers_a = _device_headers("tenant-a")
    activation_a = m5_client.post(
        "/api/v1/devices/ota/check", headers=headers_a, json=_system_info()
    ).json()["activation"]
    assert m5_client.post(
        "/api/v1/control/devices/bind",
        headers=owner_a,
        json={"agent_id": agent_a, "code": activation_a["code"]},
    ).status_code == 200

    users = UserRepository(_database())
    users.ensure_bootstrap("tenant-b@example.test", "tenant-b-password-long-enough")
    login_b = m5_client.post(
        "/api/v1/control/auth/login",
        json={
            "email": "tenant-b@example.test",
            "password": "tenant-b-password-long-enough",
        },
    )
    owner_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
    upload_b = m5_client.post(
        "/api/v1/control/ota/artifacts",
        headers={**owner_b, "Content-Type": "application/octet-stream"},
        content=b"tenant-b-firmware",
    )
    release_b = m5_client.post(
        "/api/v1/control/ota/releases",
        headers=owner_b,
        json={
            "artifact_id": upload_b.json()["id"],
            "version": "9.0.0",
            "board": "bread-compact-wifi-lcd",
            "chip": "esp32s3",
            "partition": "app",
            "force": True,
        },
    )
    assert release_b.status_code == 201
    assert m5_client.post(
        f"/api/v1/control/ota/releases/{release_b.json()['id']}/publish", headers=owner_b
    ).status_code == 200

    response_a = m5_client.post(
        "/api/v1/devices/ota/check", headers=headers_a, json=_system_info()
    ).json()
    assert response_a["firmware"] == {"version": "", "url": ""}


def test_force_release_uses_firmware_numeric_wire_type(m5_client: TestClient) -> None:
    auth = _auth(m5_client)
    agent_id = _agent(m5_client, auth)
    device_headers = _device_headers("force-wire")
    activation = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["activation"]
    assert m5_client.post(
        "/api/v1/control/devices/bind",
        headers=auth,
        json={"agent_id": agent_id, "code": activation["code"]},
    ).status_code == 200
    upload = m5_client.post(
        "/api/v1/control/ota/artifacts",
        headers={**auth, "Content-Type": "application/octet-stream"},
        content=b"force-wire-firmware",
    )
    release = m5_client.post(
        "/api/v1/control/ota/releases",
        headers=auth,
        json={
            "artifact_id": upload.json()["id"],
            "version": "1.0.0",
            "board": "bread-compact-wifi-lcd",
            "chip": "esp32s3",
            "partition": "app",
            "force": True,
        },
    )
    assert m5_client.post(
        f"/api/v1/control/ota/releases/{release.json()['id']}/publish", headers=auth
    ).status_code == 200

    firmware = m5_client.post(
        "/api/v1/devices/ota/check", headers=device_headers, json=_system_info()
    ).json()["firmware"]
    assert firmware["force"] == 1
    assert type(firmware["force"]) is int
