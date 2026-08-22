"""PostgreSQL-backed control-plane to live-device MCP integration test."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.persistence import DatabaseConfig, PostgresDatabase

TEST_DATABASE_DSN = os.environ.get("VEETEE_TEST_DATABASE_DSN", "dbname=veetee_test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"


def _database() -> PostgresDatabase:
    if "veetee_test" not in TEST_DATABASE_DSN:
        raise RuntimeError("M6.7 tests require an isolated veetee_test database")
    return PostgresDatabase(DatabaseConfig(TEST_DATABASE_DSN))


def _prepare_database(database: PostgresDatabase) -> None:
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


@pytest.fixture
def mcp_client() -> TestClient:
    database = _database()
    if not database.check():
        pytest.skip("PostgreSQL is unavailable")
    _prepare_database(database)
    app = create_app(
        Settings(
            environment="test",
            persistence_enabled=True,
            database_dsn=TEST_DATABASE_DSN,
            ota_public_base_url="http://ota.example.test",
            bootstrap_admin_email="owner@example.test",
            bootstrap_admin_password="a-test-password-long-enough",
            device_gateway_token="test-device-token",
            device_mcp_call_timeout_seconds=1,
        )
    )
    with TestClient(app) as client:
        yield client


def _login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/control/auth/login",
        json={
            "email": "owner@example.test",
            "password": "a-test-password-long-enough",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_owner_can_confirm_one_live_device_call_once_without_auditing_payload(
    mcp_client: TestClient,
) -> None:
    headers = _login(mcp_client)
    agent = mcp_client.post(
        "/api/v1/control/agents", headers=headers, json={"name": "Device Tool Agent"}
    ).json()
    device_pk = uuid4()
    with _database().connection() as connection:
        owner_id = connection.execute(
            "SELECT id FROM veetee_users WHERE email = %s", ("owner@example.test",)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO veetee_devices "
            "(id, owner_user_id, agent_id, device_id, client_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (device_pk, owner_id, agent["id"], "mcp-device-1", "mcp-client-1"),
        )

    websocket_headers = {
        "Authorization": "Bearer test-device-token",
        "Protocol-Version": "1",
        "Device-Id": "mcp-device-1",
        "Client-Id": "mcp-client-1",
    }
    hello = {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"mcp": True},
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    with mcp_client.websocket_connect(
        "/api/v1/devices/ws", headers=websocket_headers
    ) as websocket:
        websocket.send_json(hello)
        session_id = websocket.receive_json()["session_id"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            list_future = executor.submit(
                mcp_client.post,
                f"/api/v1/control/devices/{device_pk}/mcp/tools/list",
                headers=headers,
                json={"session_id": session_id},
            )
            initialize = websocket.receive_json()
            assert initialize["payload"]["method"] == "initialize"
            websocket.send_json(
                {
                    "type": "mcp",
                    "session_id": session_id,
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": initialize["payload"]["id"],
                        "result": {"protocolVersion": "2024-11-05"},
                    },
                }
            )
            listed_request = websocket.receive_json()
            assert listed_request["payload"]["method"] == "tools/list"
            websocket.send_json(
                {
                    "type": "mcp",
                    "session_id": session_id,
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": listed_request["payload"]["id"],
                        "result": {
                            "tools": [
                                {
                                    "name": "self.volume.set",
                                    "description": "Set volume",
                                    "inputSchema": {"type": "object"},
                                }
                            ]
                        },
                    },
                }
            )
            listed = list_future.result(timeout=2)
        assert listed.status_code == 200
        assert listed.json()["tools"][0]["name"] == "self.volume.set"

        prepared = mcp_client.post(
            f"/api/v1/control/devices/{device_pk}/mcp/tools/self.volume.set/prepare-call",
            headers=headers,
            json={
                "session_id": session_id,
                "arguments": {"volume": 42, "secret": "must-not-be-audited"},
            },
        )
        assert prepared.status_code == 200
        token = prepared.json()["confirmation_token"]
        with ThreadPoolExecutor(max_workers=1) as executor:
            call_future = executor.submit(
                mcp_client.post,
                f"/api/v1/control/devices/{device_pk}/mcp/tools/self.volume.set/call",
                headers=headers,
                json={"confirmation_token": token},
            )
            called_request = websocket.receive_json()
            assert called_request["payload"]["method"] == "tools/call"
            websocket.send_json(
                {
                    "type": "mcp",
                    "session_id": session_id,
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": called_request["payload"]["id"],
                        "result": {
                            "content": [{"type": "text", "text": "volume updated"}],
                            "isError": False,
                        },
                    },
                }
            )
            called = call_future.result(timeout=2)
        assert called.status_code == 200
        assert called.json()["is_error"] is False
        replay = mcp_client.post(
            f"/api/v1/control/devices/{device_pk}/mcp/tools/self.volume.set/call",
            headers=headers,
            json={"confirmation_token": token},
        )
        assert replay.status_code == 403

    with _database().connection() as connection:
        audit_text = str(
            connection.execute(
                "SELECT action, metadata FROM veetee_audit_events "
                "WHERE action LIKE 'device.mcp.%' ORDER BY created_at"
            ).fetchall()
        ).lower()
    assert "must-not-be-audited" not in audit_text
    assert token.lower() not in audit_text
    assert "volume updated" not in audit_text
