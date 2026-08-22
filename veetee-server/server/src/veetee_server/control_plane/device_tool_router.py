"""Console endpoints listing and calling online-device MCP tools (M6.7).

Locked decision for this milestone:

- ``tools/list`` against a live device needs no user confirmation but stays
  scoped to the canonical tenant owner, the persisted device row and one live
  WebSocket session;
- every ``tools/call`` requires a one-time confirmation token prepared at most
  ``VEETEE_DEVICE_MCP_CONFIRMATION_TTL_SECONDS`` (default 60s) earlier. The
  token is random, returned once in plaintext, and stored server-side only as
  a SHA-256 hash together with the canonical owner/device/session/tool/
  exact-arguments tuple inside a bounded in-memory store;
- default deny applies whenever the device is offline, its hello did not
  negotiate ``features.mcp=true``, or the live session binding does not match
  the persisted owner/client/agent identity exactly;
- audit metadata records identifiers and outcome only - never tool arguments,
  results or secret material;
- address book / device calling parity items are explicitly out of scope.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.websockets import WebSocket

from veetee_server.device_gateway import DeviceSessionRegistry
from veetee_server.device_gateway.mcp_broker import (
    DeviceMCPBroker,
    DeviceMCPBrokerError,
    DeviceMCPCallResult,
    DeviceMCPDisconnectedError,
    DeviceMCPTimeoutError,
    DeviceMCPToolError,
)
from veetee_server.domain.session import DeviceSession
from veetee_server.persistence import DeviceRepository, StoredDevice, record_audit

from .router import CurrentUser
from .schemas import (
    DeviceMcpConfirmCallRequest,
    DeviceMcpPrepareCallRequest,
    DeviceMcpToolsListRequest,
)

router = APIRouter(prefix="/api/v1/control/devices", tags=["control-plane-device-tools"])

_MCP_TOOLS_LIST_LIMIT = 100
_MCP_TOOLS_LIST_MAX_PAGES = 10
_MCP_RESULT_MAX_BYTES = 65536
_TOOL_NAME_MAX_LENGTH = 256


@dataclass(frozen=True, slots=True)
class DeviceMCPConfirmation:
    """Immutable snapshot bound at prepare time and replayed at execute time."""

    owner_user_id: UUID
    device_pk: UUID
    device_id: str
    client_id: str
    agent_id: UUID | None
    session_id: str
    tool_name: str
    arguments: dict[str, Any]
    binding_sha256: str
    expires_at: float


class DeviceMCPConfirmationStoreFullError(Exception):
    """The bounded confirmation store rejected another pending preparation."""


class DeviceMCPConfirmationStore:
    """Bounded in-memory store of hashed one-time confirmation tokens."""

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("confirmation TTL must be positive")
        if max_entries <= 0:
            raise ValueError("max entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, DeviceMCPConfirmation] = {}

    def prepare(
        self,
        *,
        owner_user_id: UUID,
        device_pk: UUID,
        device_id: str,
        client_id: str,
        agent_id: UUID | None,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        binding_sha256: str,
    ) -> tuple[str, float]:
        """Stores a new hashed entry and returns the one-time plaintext token."""
        now = monotonic()
        self._purge(now)
        if len(self._entries) >= self._max_entries:
            raise DeviceMCPConfirmationStoreFullError("Too many pending confirmations")
        # Collision-safe generation: regenerate until the hash is free so a
        # (cryptographically impossible) collision can never overwrite or
        # evict an existing pending confirmation.
        while True:
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if token_hash not in self._entries:
                break
        self._entries[token_hash] = DeviceMCPConfirmation(
            owner_user_id=owner_user_id,
            device_pk=device_pk,
            device_id=device_id,
            client_id=client_id,
            agent_id=agent_id,
            session_id=session_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            binding_sha256=binding_sha256,
            expires_at=now + self._ttl_seconds,
        )
        return token, self._ttl_seconds

    def consume(self, token: str) -> DeviceMCPConfirmation | None:
        """Pops a valid unexpired entry exactly once; everything else is None."""
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched_hash = next(
            (
                stored_hash
                for stored_hash in self._entries
                if hmac.compare_digest(stored_hash, token_hash)
            ),
            None,
        )
        record = self._entries.pop(matched_hash, None) if matched_hash is not None else None
        if record is None or monotonic() > record.expires_at:
            return None
        return record

    def _purge(self, now: float) -> None:
        expired = [key for key, item in self._entries.items() if item.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def _store(request: Request) -> DeviceMCPConfirmationStore:
    store = getattr(request.app.state, "device_mcp_confirmation_store", None)
    if not isinstance(store, DeviceMCPConfirmationStore):
        raise HTTPException(status_code=503, detail="Device MCP is not available")
    return store


def _broker(request: Request) -> DeviceMCPBroker:
    broker = getattr(request.app.state, "device_mcp_broker", None)
    if not isinstance(broker, DeviceMCPBroker):
        raise HTTPException(status_code=503, detail="Device MCP is not available")
    return broker


def _registry(request: Request) -> DeviceSessionRegistry:
    registry = getattr(request.app.state, "device_session_registry", None)
    if not isinstance(registry, DeviceSessionRegistry):
        raise HTTPException(status_code=503, detail="Session registry is unavailable")
    return registry


def _device_repository(request: Request) -> DeviceRepository:
    repository = getattr(request.app.state, "device_repository", None)
    if not isinstance(repository, DeviceRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


async def _audit(
    request: Request, user_id: UUID, action: str, device_pk: UUID, **meta: Any
) -> None:
    repository = _device_repository(request)
    metadata = {key: value for key, value in meta.items() if value is not None}
    await asyncio.to_thread(
        record_audit,
        repository.database,
        user_id,
        action,
        "device",
        str(device_pk),
        metadata,
    )


async def _resolve_authorized_session(
    request: Request,
    user_id: UUID,
    device_pk: UUID,
    requested_session_id: UUID | None,
) -> tuple[StoredDevice, DeviceSession, WebSocket]:
    """Applies the full default-deny gate chain shared by all three endpoints."""
    device = await asyncio.to_thread(_device_repository(request).get, user_id, device_pk)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    candidates = [
        (session, websocket)
        for session, websocket in await _registry(request).find_device_sessions(
            device.device_id
        )
        if session.client_id == device.client_id
    ]
    if requested_session_id is not None:
        wanted = str(requested_session_id)
        candidates = [(s, ws) for s, ws in candidates if str(s.id) == wanted]
        if not candidates:
            raise HTTPException(
                status_code=409, detail="Requested session is not live for this device"
            )
    elif len(candidates) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple live sessions; pass session_id explicitly",
        )
    if not candidates:
        raise HTTPException(status_code=409, detail="Device is offline")

    session, websocket = candidates[0]
    # Default deny on any binding mismatch: the live session must still be
    # owned by the requesting user and resolved to the same persisted agent.
    if session.owner_user_id != user_id or session.agent_id != device.agent_id:
        raise HTTPException(status_code=403, detail="Device session binding mismatch")
    if not bool(session.features.get("mcp", False)):
        raise HTTPException(status_code=403, detail="Device did not negotiate MCP support")
    return device, session, websocket


def _validate_arguments_size(arguments: dict[str, Any], max_bytes: int) -> None:
    try:
        serialized = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Tool arguments must be valid JSON") from exc
    if len(serialized.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=413, detail="Tool arguments exceed the size limit")


def _validate_tool_name(tool_name: str) -> None:
    if not tool_name or len(tool_name) > _TOOL_NAME_MAX_LENGTH:
        raise HTTPException(status_code=422, detail="Invalid tool name")


def _bounded_tools(result: Any) -> list[dict[str, Any]]:
    raw = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    bounded: list[dict[str, Any]] = []
    for item in raw[:_MCP_TOOLS_LIST_LIMIT]:
        if not isinstance(item, dict):
            continue
        bounded.append(
            {
                "name": str(item.get("name", ""))[:_TOOL_NAME_MAX_LENGTH],
                "description": str(item.get("description", "")),
                "inputSchema": item.get("inputSchema", {}),
            }
        )
    return bounded


async def _list_tools_paginated(
    broker: DeviceMCPBroker, session: DeviceSession, websocket: WebSocket
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(_MCP_TOOLS_LIST_MAX_PAGES):
        params = {"cursor": cursor} if cursor is not None else {}
        response = await broker.call_method(
            str(session.id), websocket, method="tools/list", params=params
        )
        tools.extend(_bounded_tools(response.result))
        if len(tools) >= _MCP_TOOLS_LIST_LIMIT:
            return tools[:_MCP_TOOLS_LIST_LIMIT]
        next_cursor = (
            response.result.get("nextCursor") if isinstance(response.result, dict) else None
        )
        if next_cursor is None:
            return tools
        if (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > 256
            or next_cursor in seen_cursors
        ):
            raise DeviceMCPBrokerError("Invalid device MCP pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise DeviceMCPBrokerError("Device MCP pagination exceeded the page limit")


def _bounded_result(result: Any) -> dict[str, Any]:
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return {
            "content": [{"type": "text", "text": "Device returned an invalid result"}],
            "is_error": True,
            "truncated": False,
        }
    truncated = len(encoded.encode("utf-8")) > _MCP_RESULT_MAX_BYTES
    if truncated:
        return {
            "content": [{"type": "text", "text": "Result exceeded the size limit"}],
            "is_error": True,
            "truncated": True,
        }
    content = result.get("content") if isinstance(result, dict) else None
    return {
        "content": content if isinstance(content, list) else [],
        "is_error": bool(isinstance(result, dict) and result.get("isError", False)),
        "truncated": False,
    }


@router.post("/{device_pk}/mcp/tools/list")
async def list_device_mcp_tools(
    device_pk: UUID,
    payload: DeviceMcpToolsListRequest,
    user_id: CurrentUser,
    request: Request,
) -> dict[str, Any]:
    _device, session, websocket = await _resolve_authorized_session(
        request, user_id, device_pk, payload.session_id
    )
    broker = _broker(request)
    try:
        await broker.ensure_initialized(str(session.id), websocket)
        tools = await _list_tools_paginated(broker, session, websocket)
    except DeviceMCPTimeoutError as exc:
        await _audit(request, user_id, "device.mcp.list.timeout", device_pk)
        raise HTTPException(status_code=504, detail="Device MCP call timed out") from exc
    except DeviceMCPDisconnectedError as exc:
        await _audit(request, user_id, "device.mcp.list.disconnect", device_pk)
        raise HTTPException(status_code=409, detail="Device disconnected during call") from exc
    except DeviceMCPBrokerError as exc:
        await _audit(request, user_id, "device.mcp.list.error", device_pk)
        raise HTTPException(status_code=502, detail="Device MCP call failed") from exc
    await _audit(request, user_id, "device.mcp.list.ok", device_pk, session_id=str(session.id))
    return {
        "session_id": str(session.id),
        "tools": tools,
    }


@router.post("/{device_pk}/mcp/tools/{tool_name}/prepare-call")
async def prepare_device_mcp_call(
    device_pk: UUID,
    tool_name: str,
    payload: DeviceMcpPrepareCallRequest,
    user_id: CurrentUser,
    request: Request,
) -> dict[str, Any]:
    _validate_tool_name(tool_name)
    settings = getattr(request.app.state, "settings", None)
    max_argument_bytes = int(getattr(settings, "device_mcp_max_arguments_bytes", 8192))
    _validate_arguments_size(payload.arguments, max_argument_bytes)

    device, session, _websocket = await _resolve_authorized_session(
        request, user_id, device_pk, payload.session_id
    )

    binding_digest = _canonical_binding_digest(
        owner_user_id=user_id,
        device_pk=device_pk,
        device_id=device.device_id,
        client_id=device.client_id,
        agent_id=device.agent_id,
        session_id=str(session.id),
        tool_name=tool_name,
        arguments=payload.arguments,
    )

    store = _store(request)
    try:
        token, ttl_seconds = store.prepare(
            owner_user_id=user_id,
            device_pk=device_pk,
            device_id=device.device_id,
            client_id=device.client_id,
            agent_id=device.agent_id,
            session_id=str(session.id),
            tool_name=tool_name,
            arguments=payload.arguments,
            binding_sha256=binding_digest,
        )
    except DeviceMCPConfirmationStoreFullError as exc:
        raise HTTPException(
            status_code=429, detail="Too many pending confirmations; retry later"
        ) from exc

    await _audit(
        request,
        user_id,
        "device.mcp.prepare",
        device_pk,
        session_id=str(session.id),
        tool_name=tool_name,
        binding_sha256=binding_digest,
    )
    # The plaintext token is returned exactly once and never stored server-side.
    return {
        "confirmation_token": token,
        "expires_in_seconds": round(ttl_seconds, 3),
        "session_id": str(session.id),
        "tool_name": tool_name,
        "binding_sha256": binding_digest,
    }


@router.post("/{device_pk}/mcp/tools/{tool_name}/call")
async def confirm_device_mcp_call(
    device_pk: UUID,
    tool_name: str,
    payload: DeviceMcpConfirmCallRequest,
    user_id: CurrentUser,
    request: Request,
) -> dict[str, Any]:
    _validate_tool_name(tool_name)
    record = _store(request).consume(payload.confirmation_token)
    if record is None:
        raise HTTPException(
            status_code=403, detail="Confirmation token is invalid, expired or already used"
        )
    # Every binding field must match the current request exactly.
    if (
        record.owner_user_id != user_id
        or record.device_pk != device_pk
        or record.tool_name != tool_name
    ):
        raise HTTPException(status_code=403, detail="Confirmation does not match this request")

    try:
        prepared_session_id = UUID(record.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Confirmation binding is invalid") from exc
    device, session, websocket = await _resolve_authorized_session(
        request, user_id, device_pk, prepared_session_id
    )
    current_digest = _canonical_binding_digest(
        owner_user_id=user_id,
        device_pk=device_pk,
        device_id=device.device_id,
        client_id=device.client_id,
        agent_id=device.agent_id,
        session_id=str(session.id),
        tool_name=tool_name,
        arguments=record.arguments,
    )
    if (
        record.device_id != device.device_id
        or record.client_id != device.client_id
        or record.agent_id != device.agent_id
        or str(session.id) != record.session_id
        or not hmac.compare_digest(record.binding_sha256, current_digest)
    ):
        raise HTTPException(
            status_code=403,
            detail="Confirmation binding no longer matches the live device session",
        )

    broker = _broker(request)
    outcome_action = "device.mcp.call.error"
    try:
        result: DeviceMCPCallResult = await broker.call_tool(
            str(session.id),
            websocket,
            tool_name=record.tool_name,
            arguments=record.arguments,
        )
    except DeviceMCPTimeoutError as exc:
        await _audit(request, user_id, "device.mcp.call.timeout", device_pk)
        raise HTTPException(status_code=504, detail="Device MCP call timed out") from exc
    except DeviceMCPDisconnectedError as exc:
        await _audit(request, user_id, "device.mcp.call.disconnect", device_pk)
        raise HTTPException(status_code=409, detail="Device disconnected during call") from exc
    except DeviceMCPToolError as exc:
        await _audit(
            request, user_id, "device.mcp.call.tool_error", device_pk, mcp_code=exc.code
        )
        raise HTTPException(
            status_code=502, detail=f"Device returned MCP error {exc.code}"
        ) from exc
    except DeviceMCPBrokerError as exc:
        await _audit(request, user_id, outcome_action, device_pk)
        raise HTTPException(status_code=502, detail="Device MCP call failed") from exc

    await _audit(request, user_id, "device.mcp.call.ok", device_pk, session_id=str(session.id))
    return _bounded_result(result.result)


def _canonical_binding_digest(
    *,
    owner_user_id: UUID,
    device_pk: UUID,
    device_id: str,
    client_id: str,
    agent_id: UUID | None,
    session_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """Canonical SHA-256 over the full confirmation binding tuple.

    Sorted-key compact JSON makes the digest stable across processes; it is a
    one-way value safe for audit metadata and Console display.
    """
    canonical = json.dumps(
        {
            "agent_id": str(agent_id) if agent_id else None,
            "arguments": arguments,
            "client_id": client_id,
            "device_id": device_id,
            "device_pk": str(device_pk),
            "owner_user_id": str(owner_user_id),
            "session_id": session_id,
            "tool_name": tool_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
