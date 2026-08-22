"""Comprehensive tests for Veetee Device WebSocket Gateway (M1.3)."""

import asyncio
import json
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from veetee_server.app import create_app
from veetee_server.audio import parse_audio_frame
from veetee_server.config import Settings
from veetee_server.device_gateway.registry import DeviceSessionRegistry
from veetee_server.device_gateway.router import _cleanup_session
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


def test_hello_accepts_bounded_firmware_text_font_capability(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    payload = {
        **valid_hello_payload,
        "features": {"mcp": True, "glyph_push": True},
        "text_font": {
            "bundle": "font-bundle-v1",
            "charset": "vi-VN",
            "size": 16,
            "bpp": 4,
        },
    }

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(payload)
            hello = ws.receive_json()
            assert hello["type"] == "hello"


def test_firmware_detect_and_wake_word_abort_are_accepted(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]
            ws.send_json(
                {
                    "type": "listen",
                    "state": "detect",
                    "text": "Hi,ESP",
                    "session_id": session_id,
                }
            )
            ws.send_json(
                {
                    "type": "abort",
                    "reason": "wake_word_detected",
                    "session_id": session_id,
                }
            )
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json()["type"] == "pong"


def test_listen_text_is_rejected_outside_detect(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "text": "not-valid-here",
                    "session_id": session_id,
                }
            )
            assert ws.receive_json()["code"] == "veetee_invalid_input"


@pytest.mark.parametrize(
    "text_font",
    [
        {"bundle": "", "charset": "vi-VN", "size": 16, "bpp": 4},
        {"bundle": "font", "charset": "vi-VN", "size": 0, "bpp": 4},
        {"bundle": "font", "charset": "vi-VN", "size": 16, "bpp": 3},
        {"bundle": "font", "charset": "vi-VN", "size": 16, "bpp": 4, "extra": True},
    ],
)
def test_hello_rejects_invalid_text_font_capability(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
    text_font: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    payload = {**valid_hello_payload, "text_font": text_font}

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(payload)
            assert ws.receive_json()["code"] == "veetee_invalid_input"


@pytest.mark.parametrize(
    "invalid_header_mod, expected_code",
    [
        ({"Authorization": "Bearer wrong-token"}, "veetee_auth_failed"),
        ({"Authorization": "Basic wrong-token"}, "veetee_auth_failed"),
        ({"Protocol-Version": "0"}, "veetee_invalid_input"),
        ({"Protocol-Version": "4"}, "veetee_invalid_input"),
        ({"Protocol-Version": "abc"}, "veetee_invalid_input"),
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


def test_server_heartbeat_does_not_send_json_ping(
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

            timeout = ws.receive_json()
            assert timeout == {
                "code": "veetee_timeout",
                "message": "Idle timeout",
                "session_id": session_id,
            }


def test_auto_conversation_closes_after_speech_inactivity(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    settings = test_settings.model_copy(
        update={
            "idle_timeout_seconds": 10.0,
            "conversation_idle_timeout_seconds": 0.05,
            "ping_interval_seconds": 0.01,
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            hello = ws.receive_json()
            ws.send_json(
                {
                    "type": "listen",
                    "session_id": hello["session_id"],
                    "state": "start",
                    "mode": "auto",
                }
            )
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1000


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
            assert session.state == SessionState.LISTENING
            assert session.current_turn is not None

            # listen/stop advances capture into processing without tearing down
            # the connection. M1.6 attaches the fake pipeline, which returns
            # NO_UTTERANCE and cleans up the turn back to IDLE.
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
            assert session.current_turn is None


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

            assert session.state == SessionState.IDLE
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


@pytest.mark.asyncio
async def test_cleanup_unregisters_before_waiting_for_session_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = DeviceSessionRegistry()
    session = DeviceSession(device_id="device-opaque", client_id="client-opaque")
    session.accept()
    await registry.register(session, cast(Any, StubWebSocket()))
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    async def slow_close() -> None:
        close_started.set()
        await allow_close.wait()

    monkeypatch.setattr(session, "close", slow_close)
    cleanup = asyncio.create_task(_cleanup_session(session, registry))
    await close_started.wait()

    assert registry.active_count == 0
    allow_close.set()
    await cleanup


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
    await registry.register(session, cast(Any, ws))
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
    await registry.register(session, cast(Any, ws))

    await registry.unregister(str(session.id))
    await registry.unregister(str(session.id))

    assert registry.active_count == 0


@pytest.mark.asyncio
async def test_registry_close_device_revokes_only_matching_sessions() -> None:
    registry = DeviceSessionRegistry()
    target = DeviceSession(device_id="device-target", client_id="client-1")
    other = DeviceSession(device_id="device-other", client_id="client-2")
    target.accept()
    other.accept()
    target_ws = StubWebSocket()
    other_ws = StubWebSocket()
    await registry.register(target, cast(Any, target_ws))
    await registry.register(other, cast(Any, other_ws))

    assert await registry.close_device("device-target") == 1
    assert registry.active_count == 1
    assert target.state is SessionState.CLOSED
    assert target_ws.closed == [(1008, "Device unbound")]
    assert other_ws.closed == []


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


# --- M1.5 Audio binary frame integration ---

V1_FRAME = bytes.fromhex("f8fffe0102030405")
V2_FRAME = bytes.fromhex("0002000000000000000003e800000008f8fffe0102030405")
V3_FRAME = bytes.fromhex("00000008f8fffe0102030405")


def _headers_with_version(version: int) -> dict[str, str]:
    return {
        "Authorization": "Bearer secret-gateway-token",
        "Protocol-Version": str(version),
        "Device-Id": "test-device-001",
        "Client-Id": "test-client-001",
    }


def _sync_with_ping(ws: Any, session_id: str) -> None:
    """Round-trips a ping so the server has processed prior frames."""
    ws.send_json({"type": "ping", "session_id": session_id})
    assert ws.receive_json() == {"type": "pong", "session_id": session_id}


@pytest.mark.parametrize("version", [1, 2, 3])
def test_protocol_negotiation_binary_roundtrip(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
    version: int,
) -> None:
    """Handshake with Protocol-Version 1/2/3 must accept valid binary frames while LISTENING."""
    app = create_app(test_settings)
    frames = {1: V1_FRAME, 2: V2_FRAME, 3: V3_FRAME}
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/devices/ws", headers=_headers_with_version(version)
        ) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            ws.send_json({"type": "listen", "state": "start", "session_id": session_id})
            _sync_with_ping(ws, session_id)

            ws.send_bytes(frames[version])
            _sync_with_ping(ws, session_id)

            session = _first_registered_session(app)
            assert session is not None
            assert session.protocol_version == version
            assert session.ingress_queue.item_count == 1


@pytest.mark.parametrize(
    "version, frame",
    [(1, V1_FRAME), (2, V2_FRAME), (3, V3_FRAME)],
)
def test_valid_binary_frame_queued_with_metadata(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
    version: int,
    frame: bytes,
) -> None:
    """Valid frames are queued with the right metadata while listening."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/devices/ws", headers=_headers_with_version(version)
        ) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            ws.send_json({"type": "listen", "state": "start", "session_id": session_id})
            _sync_with_ping(ws, session_id)

            ws.send_bytes(frame)
            _sync_with_ping(ws, session_id)

            session = _first_registered_session(app)
            assert session is not None
            item = session.ingress_queue.drain()[0]
            assert item.payload == bytes.fromhex("f8fffe0102030405")
            assert item.duration_ms == 60.0
            if version == 2:
                assert item.timestamp_ms == 1000
            else:
                assert item.timestamp_ms is None


def test_malformed_binary_frame_closes_1002(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """Truncated/structurally invalid frames raise protocol error and close 1002."""
    app = create_app(test_settings)
    # Negotiated v2 but the header is truncated (2 bytes only).
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(2)) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_bytes(b"\x00\x02")
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert "Malformed" in err["message"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1002


def test_protocol_version_mismatch_binary_closes_1002(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """Negotiated v2 receiving a v3 header version is a protocol mismatch -> 1002."""
    app = create_app(test_settings)
    v3_header_frame = bytes.fromhex("0003000000000000000003e800000008f8fffe0102030405")
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(2)) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_bytes(v3_header_frame)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1002


def test_oversized_binary_frame_closes_1009(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """Frame length above binary_max_bytes closes with 1009 (message too big)."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(1)) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_bytes(b"\x00" * 2000)  # binary_max_bytes=1024 in fixture
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert "size limit" in err["message"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1009


def test_oversized_declared_payload_closes_1009(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """Declared V2 payload size above the max is rejected even if the frame is short."""
    app = create_app(test_settings)
    # Header declares payload_size = 0x00010001 (65537) > binary_max_bytes=1024.
    oversized_v2 = bytes.fromhex("0002000000000000000003e800010001f8")
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(2)) as ws:
            ws.send_json(valid_hello_payload)
            ws.receive_json()

            ws.send_bytes(oversized_v2)
            err = ws.receive_json()
            assert err["code"] == "veetee_invalid_input"
            assert "size limit" in err["message"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1009


def test_abort_purges_stale_audio_from_queue(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """Abort bumps queue generation so in-flight frames are dropped immediately."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(1)) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]

            ws.send_json(
                {"type": "listen", "state": "start", "mode": "auto", "session_id": session_id}
            )
            _sync_with_ping(ws, session_id)

            ws.send_bytes(V1_FRAME)
            _sync_with_ping(ws, session_id)

            session = _first_registered_session(app)
            assert session is not None
            assert session.ingress_queue.item_count == 1
            capture_generation = session.ingress_queue.generation

            ws.send_json({"type": "abort", "session_id": session_id})
            _sync_with_ping(ws, session_id)

            # Generation advanced and stale frames were purged.
            assert session.ingress_queue.generation == capture_generation + 1
            assert session.ingress_queue.item_count == 0
            assert session.egress_queue.generation == capture_generation + 1
            assert session.state is SessionState.IDLE


def test_session_close_cleans_up_queues_and_websocket(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
) -> None:
    """A closed session must leave both audio queues closed and unregister."""
    app = create_app(test_settings)
    registry = app.state.device_session_registry
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers_with_version(3)) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]
            ws.send_json({"type": "listen", "state": "start", "session_id": session_id})
            _sync_with_ping(ws, session_id)
            ws.send_bytes(V3_FRAME)
            _sync_with_ping(ws, session_id)

            session = _first_registered_session(app)
            assert session is not None
            assert registry.active_count == 1

            ws.send_json({"type": "goodbye", "session_id": session_id})
            assert ws.receive_json() == {"type": "goodbye", "session_id": session_id}
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1000

        # Context exit guarantees the handler finished cleanup.
        assert session.state is SessionState.CLOSED
        assert session.ingress_queue.is_closed
        assert session.egress_queue.is_closed
        assert registry.active_count == 0


@pytest.mark.parametrize("version", [1, 2, 3])
def test_fake_pipeline_end_to_end_for_each_wire_version(
    test_settings: Settings,
    valid_hello_payload: dict[str, Any],
    version: int,
) -> None:
    """A captured utterance produces ordered STT, TTS and framed audio output."""
    app = create_app(test_settings)
    frame = {1: V1_FRAME, 2: V2_FRAME, 3: V3_FRAME}[version]

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/devices/ws", headers=_headers_with_version(version)
        ) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "session_id": session_id,
                }
            )
            ws.send_bytes(frame)
            ws.send_bytes(frame)
            ws.send_json({"type": "listen", "state": "stop", "session_id": session_id})

            stt = ws.receive_json()
            assert stt["type"] == "stt"
            assert stt["session_id"] == session_id
            assert stt["text"]
            assert ws.receive_json() == {
                "type": "tts",
                "state": "start",
                "session_id": session_id,
            }
            sentence = ws.receive_json()
            assert sentence == {
                "type": "tts",
                "state": "sentence_start",
                "text": stt["text"],
                "session_id": session_id,
            }

            for _ in range(test_settings.pipeline_tts_chunks_per_sentence):
                downlink = ws.receive_bytes()
                packet = parse_audio_frame(
                    downlink,
                    negotiated_version=version,
                    max_payload_bytes=test_settings.binary_max_bytes,
                )
                assert packet.payload.startswith(b"\xf8\xff\xfe")

            assert ws.receive_json() == {
                "type": "tts",
                "state": "stop",
                "session_id": session_id,
            }

            session = _first_registered_session(app)
            assert session is not None
            assert session.state is SessionState.IDLE
            assert session.current_turn is None


def test_auto_mode_server_vad_starts_pipeline_without_listen_stop(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_hello_payload: dict[str, Any],
) -> None:
    app = create_app(
        test_settings.model_copy(
            update={
                "pipeline_vad_start_frames": 1,
                "pipeline_vad_end_silence_frames": 1,
            }
        )
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=valid_headers) as ws:
            ws.send_json(valid_hello_payload)
            session_id = ws.receive_json()["session_id"]
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "session_id": session_id,
                }
            )
            ws.send_bytes(b"\x40")
            ws.send_bytes(b"\x00")

            assert ws.receive_json()["type"] == "stt"
            assert ws.receive_json()["state"] == "start"
