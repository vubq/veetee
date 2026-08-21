"""JSON-RPC 2.0 dataclasses and standard MCP error codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class MCPErrorCode(IntEnum):
    """Standard JSON-RPC 2.0 and MCP error codes."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    STALE_SESSION = -32001
    POLICY_VIOLATION = -32002
    CONFIRMATION_REQUIRED = -32003


@dataclass(frozen=True, slots=True)
class JSONRPCError:
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            res["data"] = self.data
        return res


@dataclass(frozen=True, slots=True)
class JSONRPCRequest:
    """JSON-RPC 2.0 request object."""

    method: str
    id: str | int | None = None
    jsonrpc: str = "2.0"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JSONRPCRequest:
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object")
        if data.get("jsonrpc") != "2.0":
            raise ValueError("Invalid jsonrpc version. Must be '2.0'")
        method = data.get("method")
        if not method or not isinstance(method, str):
            raise ValueError("Field 'method' is required and must be a non-empty string")
        params = data.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("Field 'params' must be a JSON object dictionary")
        return cls(
            jsonrpc="2.0",
            method=method,
            id=data.get("id"),
            params=params,
        )


@dataclass(frozen=True, slots=True)
class JSONRPCResponse:
    """JSON-RPC 2.0 response object."""

    id: str | int | None
    jsonrpc: str = "2.0"
    result: Any | None = None
    error: JSONRPCError | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            res["error"] = self.error.to_dict()
        else:
            res["result"] = self.result
        return res
