"""Comprehensive tests for Veetee Device WebSocket Gateway (M1.3)."""

import json
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.device_gateway.registry import DeviceSessionRegistry
from veetee_server.domain.session import DeviceSession, SessionState


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_name="gateway-test-server",
        environment="test",
        device_gateway_token="secret-gateway-token",
        hello_timeout_seconds=0.5,
        idle_timeout_seconds=0.5,
        ping_interval_seconds=0.4,
        pong_timeout_seconds=0.2,
        json_max_bytes=512,
        json_max_depth=4,
        binary_max_bytes=1024,
        id_max_length=64,
    )


@pytest.fixture
def valid_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer secret-gateway-token",
        "Protocol-Version": "1",
        "Device-Id": "test-device-001",
        "Client-Id": "test-client-001",
    }


@pytest.fixture
def valid_hello_payload() -> dict[str, Any]:
    return {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"aec": True, "mcp": True},
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }


def _first_registered_session(app: Any) -> DeviceSession | None:
    """Reads the first registered session for direct state assertions.

    The registry is asyncio-single-threaded and the TestClient blocks until the
    server handler processes each frame, so this point-in-time read is stable.
    """
    registry: DeviceSessionRegistry = app.state.device_session_registry
    sessions = list(registry._sessions.values())
    if not sessions:
        return None
    return sessions[0][0]


def test_happy_path_handshake_and_hello(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            res = ws.receive_json()

            assert res["type"] == "hello"
            assert res["transport"] == "websocket"
            assert res["audio_params"] == {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            }
            # Verify opaque UUID
            session_id = res["session_id"]
            UUID(session_id)

            # Clean goodbye close
            ws.send_json({"type": "goodbye", "session_id": session_id})
            close_resp = ws.receive_json()
            assert close_resp["type"] == "goodbye"

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1000


@pytest.mark.parametrize(
    "invalid_header_mod, expected_code",
    [
        ({"Authorization": "Bearer wrong-token"}, "veetee_auth_failed"),
        ({"Authorization": "Basic wrong-token"}, "veetee_auth_failed"),
        ({"Protocol-Version": "2"}, "veetee_invalid_input"),
        ({"Device-Id": ""}, "veetee_invalid_input"),
        ({"Client-Id": ""}, "veetee_invalid_input"),
        ({"Device-Id": "a" * 129}, "veetee_invalid_input"),
    ],
)
def test_invalid_handshake_headers(
    test_settings: Settings,
    valid_headers: dict[str, str],
    invalid_header_mod: dict[str, str],
    expected_code: str,
) -> None:
    app = create_app(test_settings)
    headers = {**valid_headers, **invalid_header_mod}
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=headers) as ws:
            err = ws.receive_json()
            assert err["code"] == expected_code
            assert err["session_id"] is None

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1008


def test_hello_timeout(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    # Use short timeout
    short_settings = test_settings.model_copy(update={"hello_timeout_seconds": 0.1})
    app = create_app(short_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            # Send nothing and wait for timeout
            err = ws.receive_json()
            assert err["code"] == "veetee_timeout"
            assert err["message"] == "Hello timeout"
            assert err["session_id"] is None


def test_binary_frame_before_hello(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_bytes(b"\x00\x01\x02\x03")
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert err["message"] == "Binary frame received before hello"
            assert err["session_id"] is None

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1002


def test_malformed_and_deep_json(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # Malformed JSON syntax
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_text("{bad_json: true")
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"

        # Exceeds max depth limit (max_depth=4 in fixture)
        deep_json = json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}})
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_text(deep_json)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"


def test_oversized_json_frame(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        large_json = json.dumps({"type": "hello", "padding": "x" * 600})
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_text(large_json)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert "size limit" in err["message"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1009


def test_invalid_hello_audio_params(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    # Wrong sample rate
    bad_payload = valid_hello_payload.copy()
    bad_payload["audio_params"] = {
        "format": "opus",
        "sample_rate": 24000,  # Expected 16000
        "channels": 1,
        "frame_duration": 60,
    }
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(bad_payload)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"


def test_duplicate_hello_rejected(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            hello_res = ws.receive_json()

            # Second hello message
            ws.send_json(valid_hello_payload)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert err["message"] == "Duplicate hello message"
            assert err["session_id"] == hello_res["session_id"]


def test_session_id_mismatch(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_json({"type": "ping", "session_id": "wrong-session-uuid"})
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert err["message"] == "Session ID mismatch"


def test_ping_pong_and_goodbye_flow(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            res = ws.receive_json()
            session_id = res["session_id"]

            # Ping
            ws.send_json({"type": "ping", "session_id": session_id})
            pong = ws.receive_json()
            assert pong == {"type": "pong", "session_id": session_id}

            # Heartbeat Pong
            ws.send_json({"type": "pong", "session_id": session_id})

            # Goodbye
            ws.send_json({"type": "goodbye", "session_id": session_id})
            goodbye = ws.receive_json()
            assert goodbye == {"type": "goodbye", "session_id": session_id}


def test_listen_and_abort_turn_flow(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            res = ws.receive_json()
            session_id = res["session_id"]

            registry = app.state.device_session_registry
            assert registry.active_count == 1

            # Listen start
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "session_id": session_id,
                }
            )
            # Give short moment for async task state transition
            # Session should be in LISTENING state now
            # Send Abort
            ws.send_json({"type": "abort", "session_id": session_id})

            # Listen stop
            ws.send_json(
                {
                    "type": "listen",
                    "state": "stop",
                    "session_id": session_id,
                }
            )

            # Close gracefully
            ws.send_json({"type": "goodbye", "session_id": session_id})
            ws.receive_json()


def test_binary_frames_after_hello(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # Binary frame <= binary_max_bytes (1024 bytes)
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            ws.send_bytes(b"\x01\x02\x03\x04" * 10)  # 40 bytes - valid

            # Check websocket is still alive with ping
            ws.send_json({"type": "ping", "session_id": session_id})
            pong = ws.receive_json()
            assert pong["type"] == "pong"

        # Binary frame > binary_max_bytes
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_bytes(b"\x00" * 2000)  # Exceeds 1024
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert "size limit" in err["message"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1009


def test_unsupported_mcp_message_type(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            ws.send_json({"type": "mcp", "session_id": session_id, "payload": {}})
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert err["message"] == "Unsupported frame type"
            assert "mcp" not in err["message"]

            # Connection remains open after safe error
            ws.send_json({"type": "ping", "session_id": session_id})
            pong = ws.receive_json()
            assert pong["type"] == "pong"


def test_idle_timeout(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    short_idle_settings = test_settings.model_copy(
        update={
            "idle_timeout_seconds": 0.1,
            "ping_interval_seconds": 0.2,
            "pong_timeout_seconds": 0.05,
        }
    )
    app = create_app(short_idle_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            # Idle timeout message
            err = ws.receive_json()
            assert err["code"] == "veetee_timeout"
            assert err["message"] == "Idle timeout"
            assert err["session_id"] == session_id

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1001


def test_server_heartbeat_requires_pong(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    heartbeat_settings = test_settings.model_copy(
        update={
            "idle_timeout_seconds": 1.0,
            "ping_interval_seconds": 0.05,
            "pong_timeout_seconds": 0.05,
        }
    )
    app = create_app(heartbeat_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            assert ws.receive_json() == {"type": "ping", "session_id": session_id}
            timeout = ws.receive_json()
            assert timeout == {
                "code": "veetee_timeout",
                "message": "Pong timeout",
                "session_id": session_id,
            }


def test_strict_schema_rejects_extra_fields_without_echo(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    payload = {**valid_hello_payload, "sensitive_input": "must-not-echo"}
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(payload)
            error = ws.receive_json()

    assert error["code"] == "veetee_invalid_input"
    assert "must-not-echo" not in json.dumps(error)


def test_session_isolation_and_reconnect(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # First session
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws1:
            ws1.send_json(valid_hello_payload)
            sid1 = ws1.receive_json()["session_id"]

            # Second session
            headers2 = valid_headers.copy()
            headers2["Client-Id"] = "test-client-002"
            with client.websocket_connect("/api/v1/devices/ws", headers=headers2) as ws2:
                ws2.send_json(valid_hello_payload)
                sid2 = ws2.receive_json()["session_id"]

                assert sid1 != sid2
                registry = app.state.device_session_registry
                assert registry.active_count == 2


def test_session_state_machine_after_hello_and_listen(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            res = ws.receive_json()
            session_id = res["session_id"]

            session = _first_registered_session(app)
            assert session is not None
            # Successful hello must move the DeviceSession out of CONNECTING.
            assert session.state is SessionState.IDLE

            # listen/start transitions the session into LISTENING.
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "session_id": session_id,
                }
            )
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json() == {"type": "pong", "session_id": session_id}
            assert session.state is SessionState.LISTENING
            assert session.current_turn is not None

            # listen/stop advances capture into processing without tearing down
            # the connection. M1.6 will attach the fake pipeline to this state.
            ws.send_json(
                {
                    "type": "listen",
                    "state": "stop",
                    "session_id": session_id,
                }
            )
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json()["type"] == "pong"
            assert session.state is SessionState.IDLE
            assert session.current_turn is not None


def test_abort_cancels_active_turn(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            res = ws.receive_json()
            session_id = res["session_id"]

            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "session_id": session_id,
                }
            )
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json()["type"] == "pong"

            session = _first_registered_session(app)
            assert session is not None
            assert session.state is SessionState.LISTENING

            # Abort must cancel the active turn and return the session to IDLE
            # without closing the connection.
            ws.send_json({"type": "abort", "session_id": session_id})
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json()["type"] == "pong"

            assert session.state is SessionState.IDLE
            assert session.current_turn is None


def test_registry_cleanup_and_reconnect(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    registry = app.state.device_session_registry
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            first_sid = ws.receive_json()["session_id"]
            assert registry.active_count == 1

        # The WebSocketTestSession context manager blocks until the server
        # handler finishes, so the session must already be unregistered.
        assert registry.active_count == 0

        # Reconnect with the same device/client identity must create a fresh
        # session and leave no stale entry behind.
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            second_sid = ws.receive_json()["session_id"]
            assert registry.active_count == 1

        assert second_sid != first_sid
        assert registry.active_count == 0


class StubWebSocket:
    """Minimal stand-in for a Starlette WebSocket used by registry tests."""

    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


@pytest.mark.asyncio
async def test_registry_close_all_closes_sessions_and_websockets() -> None:
    registry = DeviceSessionRegistry()
    session = DeviceSession(device_id="device-opaque", client_id="client-opaque")
    session.accept()
    ws = StubWebSocket()
    await registry.register(session, ws)
    assert registry.active_count == 1

    await registry.close_all(code=1012, reason="Server shutdown")

    assert registry.active_count == 0
    assert session.state is SessionState.CLOSED
    assert ws.closed == [(1012, "Server shutdown")]


@pytest.mark.asyncio
async def test_registry_unregister_is_idempotent() -> None:
    registry = DeviceSessionRegistry()
    session = DeviceSession(device_id="device-opaque", client_id="client-opaque")
    ws = StubWebSocket()
    await registry.register(session, ws)

    await registry.unregister(str(session.id))
    await registry.unregister(str(session.id))

    assert registry.active_count == 0


def test_readiness_reflects_gateway_token_configuration() -> None:
    # 1. Non-test env with empty token -> not ready (503)
    app_no_token = create_app(Settings(environment="production", device_gateway_token=""))
    with TestClient(app_no_token) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"

    # 2. Non-test env with token configured -> ready (200)
    app_with_token = create_app(Settings(environment="production", device_gateway_token="my-token"))
    with TestClient(app_with_token) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"
