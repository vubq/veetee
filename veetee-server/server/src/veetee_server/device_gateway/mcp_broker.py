"""Device MCP call broker with bounded request/response correlation (M6.7).

The broker is the single owner of in-flight server -> device JSON-RPC 2.0
calls. Every call binds one correlation id to exactly one live session, so:

- responses are matched only within the originating session (cross-session
  injection cannot resolve a pending entry);
- unknown, duplicate or stale responses are ignored safely (debug log only,
  never an error frame back to the device);
- timeout, cancellation and disconnect cleanup always resolve the pending
  future and remove the correlation entry;
- pending calls are bounded per session; overflow fails fast instead of
  growing unbounded memory from control-plane traffic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from starlette.websockets import WebSocket

from veetee_server.device_gateway.downlink import websocket_send_lock
from veetee_server.device_gateway.protocol import (
    MCP_METHOD_MAX_LENGTH,
    build_device_mcp_request,
)

logger = logging.getLogger("veetee.device_gateway.mcp")

_CORRELATION_PREFIX = "vtmcp"


class DeviceMCPBrokerError(Exception):
    """Base error for failed device MCP round-trips."""


class DeviceMCPTimeoutError(DeviceMCPBrokerError):
    """The device did not answer before the call deadline."""


class DeviceMCPDisconnectedError(DeviceMCPBrokerError):
    """The session ended while the call was still pending."""


class DeviceMCPToolError(DeviceMCPBrokerError):
    """The device answered with a JSON-RPC error object."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"device mcp error {code}: {message}")
        self.code = code
        self.message = message


class DeviceMCPBusyError(DeviceMCPBrokerError):
    """Too many pending calls for one session (bounded store)."""


@dataclass(frozen=True, slots=True)
class DeviceMCPCallResult:
    """Successful device response payload."""

    correlation_id: str
    result: Any


@dataclass(frozen=True, slots=True)
class _PendingCall:
    future: asyncio.Future[Any]
    correlation_id: str


def canonical_tool_digest(tool_name: str, arguments: dict[str, Any]) -> str:
    """Returns the canonical SHA-256 digest binding tool name + exact arguments.

    The digest uses sorted-key compact JSON so any re-serialization of the same
    arguments yields the same value. It is safe to expose in audit metadata:
    it leaks nothing beyond what the caller already knows.
    """
    canonical = json.dumps(
        {"arguments": arguments, "tool_name": tool_name},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DeviceMCPBroker:
    """Correlates bounded JSON-RPC 2.0 requests/responses per live session."""

    def __init__(
        self, *, call_timeout_seconds: float, max_pending_per_session: int
    ) -> None:
        if call_timeout_seconds <= 0:
            raise ValueError("call timeout must be positive")
        if max_pending_per_session <= 0:
            raise ValueError("max_pending_per_session must be positive")
        self._call_timeout_seconds = call_timeout_seconds
        self._max_pending_per_session = max_pending_per_session
        self._pending: dict[str, dict[str, _PendingCall]] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._initialize_locks: dict[str, asyncio.Lock] = {}
        self._initialized_sessions: set[str] = set()

    @property
    def call_timeout_seconds(self) -> float:
        return self._call_timeout_seconds

    def pending_count(self, session_id: str) -> int:
        return len(self._pending.get(session_id, {}))

    async def ensure_initialized(self, session_id: str, websocket: WebSocket) -> None:
        """Initializes MCP once for one live device session."""
        if session_id in self._initialized_sessions:
            return
        initialize_lock = self._initialize_locks.setdefault(session_id, asyncio.Lock())
        async with initialize_lock:
            if session_id in self._initialized_sessions:
                return
            await self.call_method(
                session_id,
                websocket,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "Veetee", "version": "0.1.0"},
                },
            )
            self._initialized_sessions.add(session_id)

    async def call_method(
        self,
        session_id: str,
        websocket: WebSocket,
        *,
        method: str,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> DeviceMCPCallResult:
        """Sends one JSON-RPC request to the device and awaits the correlated result.

        Raises the typed broker errors on timeout, disconnect or JSON-RPC
        error. Every exit path removes the pending entry exactly once.
        """
        if not method or len(method) > MCP_METHOD_MAX_LENGTH:
            raise ValueError("method length out of bounds")
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")
        session_pending = self._pending.setdefault(session_id, {})
        self._purge_cancelled(session_pending)
        if len(session_pending) >= self._max_pending_per_session:
            raise DeviceMCPBusyError("Too many pending device MCP calls")

        correlation_id = f"{_CORRELATION_PREFIX}-{uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        session_pending[correlation_id] = _PendingCall(
            future=future, correlation_id=correlation_id
        )
        envelope = build_device_mcp_request(
            correlation_id=correlation_id,
            method=method,
            params=params,
            session_id=session_id,
        )
        try:
            send_lock = websocket_send_lock(websocket) or self._send_locks.setdefault(
                session_id, asyncio.Lock()
            )
            async with send_lock:
                await websocket.send_text(
                    json.dumps(envelope, ensure_ascii=False, allow_nan=False)
                )
        except Exception as exc:
            self._resolve(session_id, correlation_id, DeviceMCPDisconnectedError())
            raise DeviceMCPDisconnectedError() from exc
        try:
            response = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._call_timeout_seconds if timeout_seconds is None else timeout_seconds,
            )
        except TimeoutError as exc:
            self._resolve(session_id, correlation_id, DeviceMCPTimeoutError())
            raise DeviceMCPTimeoutError() from exc
        except asyncio.CancelledError:
            self._resolve(session_id, correlation_id, DeviceMCPDisconnectedError())
            raise
        if isinstance(response, Exception):
            raise response
        return DeviceMCPCallResult(correlation_id=correlation_id, result=response)

    async def call_tool(
        self,
        session_id: str,
        websocket: WebSocket,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> DeviceMCPCallResult:
        """Convenience wrapper issuing ``tools/call`` for one tool."""
        if not tool_name or len(tool_name) > MCP_METHOD_MAX_LENGTH * 2:
            raise ValueError("tool name length out of bounds")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
        await self.ensure_initialized(session_id, websocket)
        return await self.call_method(
            session_id,
            websocket,
            method="tools/call",
            params={"name": tool_name, "arguments": arguments},
            timeout_seconds=timeout_seconds,
        )

    def handle_response(self, session_id: str, rpc_id: str | int, result: Any) -> bool:
        """Routes a validated success result to its pending call.

        Returns ``True`` when a pending call was resolved. Unknown ids return
        ``False`` and are ignored by the caller (safe no-op).
        """
        key = self._correlation_key(rpc_id)
        pending = self._pending.get(session_id)
        if not pending:
            return False
        # Duplicate delivery: the second lookup finds nothing because the
        # entry was already removed by the first resolution.
        entry = pending.pop(key, None)
        if not pending:
            self._pending.pop(session_id, None)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.set_result(result)
        else:  # pragma: no cover - defensive: cleanup already handled it
            logger.debug(
                "mcp_duplicate_response_after_resolution",
                extra={"context": {"session_id": session_id}},
            )
        return True

    def handle_error(self, session_id: str, rpc_id: str | int, code: int, message: str) -> bool:
        """Routes a JSON-RPC error object to its pending call."""
        key = self._correlation_key(rpc_id)
        pending = self._pending.get(session_id)
        if not pending:
            return False
        entry = pending.pop(key, None)
        if not pending:
            self._pending.pop(session_id, None)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.set_result(DeviceMCPToolError(code, message))
        return True

    def cancel_session(self, session_id: str) -> None:
        """Fails every pending call of a disconnected/cancelled session."""
        pending = self._pending.pop(session_id, {})
        self._send_locks.pop(session_id, None)
        self._initialize_locks.pop(session_id, None)
        self._initialized_sessions.discard(session_id)
        for entry in pending.values():
            if not entry.future.done():
                entry.future.set_result(DeviceMCPDisconnectedError())

    def _correlation_key(self, rpc_id: str | int) -> str:
        # Correlation ids minted by this server are strings; integer ids can
        # only come from a stale/foreign device response, which then simply
        # never matches a pending entry.
        return rpc_id if isinstance(rpc_id, str) else str(rpc_id)

    def _resolve(self, session_id: str, correlation_id: str, error: Exception) -> None:
        pending = self._pending.get(session_id)
        if not pending:
            return
        entry = pending.pop(correlation_id, None)
        if not pending:
            self._pending.pop(session_id, None)
        if entry is not None and not entry.future.done():
            entry.future.set_result(error)

    def _purge_cancelled(self, session_pending: dict[str, _PendingCall]) -> None:
        """Drops entries whose futures were already resolved elsewhere.

        Empty per-session maps are removed by ``_resolve`` / ``handle_response``
        / ``handle_error`` / ``cancel_session``; this helper must never detach
        the live map because callers insert into it right afterwards.
        """
        for stale_id in [
            correlation_id
            for correlation_id, entry in session_pending.items()
            if entry.future.done()
        ]:
            session_pending.pop(stale_id, None)
