"""Focused contract and security tests for M6.7 device MCP."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from veetee_server.control_plane import device_tool_router
from veetee_server.control_plane.device_tool_router import (
    DeviceMCPConfirmationStore,
    DeviceMCPConfirmationStoreFullError,
    _bounded_result,
    _validate_arguments_size,
)
from veetee_server.device_gateway.mcp_broker import (
    DeviceMCPBroker,
    DeviceMCPBusyError,
    DeviceMCPDisconnectedError,
    DeviceMCPTimeoutError,
    DeviceMCPToolError,
)
from veetee_server.device_gateway.protocol import (
    McpMessage,
    build_device_mcp_request,
    parse_device_mcp_response,
)


class StubWebSocket:
    """Minimal socket exposing the state/send interface used by the broker."""

    def __init__(self) -> None:
        self.state = SimpleNamespace(send_lock=asyncio.Lock())
        self.sent: list[dict[str, Any]] = []
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.release_send.set()

    async def send_text(self, text: str) -> None:
        self.send_started.set()
        await self.release_send.wait()
        self.sent.append(cast(dict[str, Any], json.loads(text)))


def _confirmation_kwargs() -> dict[str, Any]:
    return {
        "owner_user_id": uuid4(),
        "device_pk": uuid4(),
        "device_id": "device-1",
        "client_id": "client-1",
        "agent_id": uuid4(),
        "session_id": str(uuid4()),
        "tool_name": "self.volume.set",
        "arguments": {"volume": 42},
        "binding_sha256": "a" * 64,
    }


def test_mcp_protocol_builder_and_strict_response_parser() -> None:
    session_id = str(uuid4())
    request = build_device_mcp_request(
        correlation_id="vtmcp-1",
        method="tools/list",
        params={},
        session_id=session_id,
    )
    assert request == {
        "type": "mcp",
        "session_id": session_id,
        "payload": {
            "jsonrpc": "2.0",
            "id": "vtmcp-1",
            "method": "tools/list",
            "params": {},
        },
    }
    assert parse_device_mcp_response(
        {"jsonrpc": "2.0", "id": "vtmcp-1", "result": {"tools": []}}
    ) == "vtmcp-1"
    assert parse_device_mcp_response(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "missing"}}
    ) == 1

    with pytest.raises(ValidationError):
        McpMessage.model_validate({"type": "mcp", "payload": {}})
    invalid_payloads = [
        {"jsonrpc": "2.0", "id": "x", "result": {}, "error": {}},
        {"jsonrpc": "2.0", "id": "x", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": True, "result": {}},
        {"jsonrpc": "2.0", "id": "x", "result": {}, "extra": "secret"},
        {"jsonrpc": "2.0", "id": "x", "error": {"code": 1, "message": "x" * 513}},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            parse_device_mcp_response(payload)


def test_mcp_shared_golden_vector_is_valid() -> None:
    vector_path = Path(__file__).parents[2] / "contracts" / "device" / "mcp_golden.json"
    vectors = json.loads(vector_path.read_text(encoding="utf-8"))
    session_id = vectors["initialize_request"]["session_id"]
    for name, envelope in vectors.items():
        assert McpMessage.model_validate(envelope).session_id == session_id
        payload = envelope["payload"]
        if name.endswith("response"):
            assert parse_device_mcp_response(payload) == payload["id"]
        else:
            assert payload["jsonrpc"] == "2.0"
            assert payload["method"] in {"initialize", "tools/list", "tools/call"}


@pytest.mark.asyncio
async def test_broker_correlates_success_error_and_initialize_once() -> None:
    broker = DeviceMCPBroker(call_timeout_seconds=1, max_pending_per_session=4)
    websocket = StubWebSocket()
    session_id = str(uuid4())

    initialize = asyncio.create_task(broker.ensure_initialized(session_id, cast(Any, websocket)))
    await asyncio.sleep(0)
    initialize_id = websocket.sent[0]["payload"]["id"]
    assert websocket.sent[0]["payload"]["method"] == "initialize"
    assert broker.handle_response(session_id, initialize_id, {"protocolVersion": "2024-11-05"})
    await initialize
    await broker.ensure_initialized(session_id, cast(Any, websocket))
    assert len(websocket.sent) == 1

    call = asyncio.create_task(
        broker.call_method(session_id, cast(Any, websocket), method="tools/list", params={})
    )
    await asyncio.sleep(0)
    call_id = websocket.sent[-1]["payload"]["id"]
    assert broker.handle_response(session_id, call_id, {"tools": []})
    assert (await call).result == {"tools": []}
    assert not broker.handle_response(session_id, call_id, {"tools": ["duplicate"]})
    assert broker.pending_count(session_id) == 0

    failed = asyncio.create_task(
        broker.call_method(session_id, cast(Any, websocket), method="tools/list", params={})
    )
    await asyncio.sleep(0)
    failed_id = websocket.sent[-1]["payload"]["id"]
    assert broker.handle_error(session_id, failed_id, -32601, "unsafe-device-detail")
    with pytest.raises(DeviceMCPToolError) as error:
        await failed
    assert error.value.code == -32601


@pytest.mark.asyncio
async def test_broker_timeout_disconnect_bound_and_session_isolation() -> None:
    broker = DeviceMCPBroker(call_timeout_seconds=0.01, max_pending_per_session=1)
    first_socket = StubWebSocket()
    second_socket = StubWebSocket()
    first_session = str(uuid4())
    second_session = str(uuid4())

    timed_out = asyncio.create_task(
        broker.call_method(first_session, cast(Any, first_socket), method="tools/list", params={})
    )
    await asyncio.sleep(0)
    correlation_id = first_socket.sent[0]["payload"]["id"]
    assert not broker.handle_response(second_session, correlation_id, {})
    with pytest.raises(DeviceMCPBusyError):
        await broker.call_method(
            first_session, cast(Any, first_socket), method="tools/list", params={}
        )
    with pytest.raises(DeviceMCPTimeoutError):
        await timed_out
    assert broker.pending_count(first_session) == 0

    disconnected = asyncio.create_task(
        broker.call_method(
            second_session, cast(Any, second_socket), method="tools/list", params={}
        )
    )
    await asyncio.sleep(0)
    broker.cancel_session(second_session)
    with pytest.raises(DeviceMCPDisconnectedError):
        await disconnected
    assert broker.pending_count(second_session) == 0


@pytest.mark.asyncio
async def test_broker_uses_shared_websocket_send_lock() -> None:
    broker = DeviceMCPBroker(call_timeout_seconds=1, max_pending_per_session=2)
    websocket = StubWebSocket()
    websocket.release_send.clear()
    session_id = str(uuid4())
    await websocket.state.send_lock.acquire()
    call = asyncio.create_task(
        broker.call_method(session_id, cast(Any, websocket), method="tools/list", params={})
    )
    await asyncio.sleep(0)
    assert not websocket.send_started.is_set()
    websocket.state.send_lock.release()
    await websocket.send_started.wait()
    websocket.release_send.set()
    await asyncio.sleep(0)
    correlation_id = websocket.sent[0]["payload"]["id"]
    broker.handle_response(session_id, correlation_id, {})
    await call


def test_confirmation_store_is_one_time_bounded_expiring_and_collision_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 100.0
    monkeypatch.setattr(device_tool_router, "monotonic", lambda: now)
    tokens = iter(["same-token", "same-token", "new-token"])
    monkeypatch.setattr(device_tool_router.secrets, "token_urlsafe", lambda _size: next(tokens))
    store = DeviceMCPConfirmationStore(ttl_seconds=60, max_entries=2)
    kwargs = _confirmation_kwargs()
    first, _ = store.prepare(**kwargs)
    second, _ = store.prepare(**{**kwargs, "device_pk": uuid4()})
    assert first == "same-token"
    assert second == "new-token"
    with pytest.raises(DeviceMCPConfirmationStoreFullError):
        store.prepare(**{**kwargs, "device_pk": uuid4()})
    assert store.consume(first) is not None
    assert store.consume(first) is None
    now = 161.0
    assert store.consume(second) is None


def test_arguments_and_results_reject_non_json_and_apply_bounds() -> None:
    _validate_arguments_size({"ok": 1}, 32)
    with pytest.raises(Exception) as oversized:
        _validate_arguments_size({"value": "x" * 64}, 8)
    assert getattr(oversized.value, "status_code", None) == 413
    with pytest.raises(Exception) as non_finite:
        _validate_arguments_size({"value": float("nan")}, 64)
    assert getattr(non_finite.value, "status_code", None) == 422
    assert _bounded_result({"content": [], "value": float("nan")})["is_error"] is True
