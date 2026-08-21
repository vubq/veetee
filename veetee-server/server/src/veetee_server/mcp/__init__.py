"""MCP JSON-RPC adapter contracts and session validation."""

from .adapter import MCPAdapter, StaleSessionError
from .protocol import (
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPErrorCode,
)

__all__ = [
    "JSONRPCError",
    "JSONRPCRequest",
    "JSONRPCResponse",
    "MCPAdapter",
    "MCPErrorCode",
    "StaleSessionError",
]
