from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    await_operation,
)

McpSender = Callable[[dict[str, Any]], Awaitable[None]]


class DeviceMcpError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceTool:
    name: str
    description: str
    input_schema: dict[str, Any]


class DeviceMcpClient:
    def __init__(self, sender: McpSender, *, session_id: str) -> None:
        self._sender = sender
        self._session_id = session_id
        self._next_id = 1
        self._pending: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._tools: dict[str, DeviceTool] = {}
        self._closed = False

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "requiresConfirmation": False,
            }
            for tool in self._tools.values()
        ]

    async def initialize(self, timeout_seconds: float = 8.0) -> None:
        context = OperationContext(
            self._session_id,
            f"{self._session_id}:mcp-bootstrap",
            0,
            CancellationToken(),
            monotonic() + timeout_seconds,
        )
        initialized = await self._request(
            "initialize", {"capabilities": {}}, context.child(timeout_seconds)
        )
        self._validate_initialize_result(initialized)
        cursor = ""
        seen_cursors: set[str] = set()
        discovered: dict[str, DeviceTool] = {}
        for _ in range(32):
            result = await self._request(
                "tools/list",
                {"cursor": cursor, "withUserTools": False},
                context,
            )
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise DeviceMcpError("Device tools/list result is missing tools")
            for item in tools:
                tool = self._parse_tool(item)
                if tool.name in discovered:
                    raise DeviceMcpError(f"Duplicate device MCP tool: {tool.name}")
                discovered[tool.name] = tool
                if len(discovered) > 128:
                    raise DeviceMcpError("Device MCP catalog exceeds 128 tools")
            next_cursor = result.get("nextCursor")
            if next_cursor in {None, ""}:
                self._tools = discovered
                return
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise DeviceMcpError("Device MCP pagination cursor is invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise DeviceMcpError("Device MCP catalog exceeds pagination limit")

    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any:
        context.checkpoint()
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown device MCP tool: {name}")
        validation_error = next(
            Draft202012Validator(tool.input_schema).iter_errors(arguments), None
        )
        if validation_error is not None:
            raise DeviceMcpError(
                f"Invalid arguments for {name}: {validation_error.message[:256]}"
            )
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments}, context
        )
        self._validate_call_result(name, result)
        if result.get("isError") is True:
            raise DeviceMcpError(f"Device MCP tool failed: {name}")
        return {"tool": name, "arguments": arguments, "result": result}

    async def handle_payload(self, payload: dict[str, Any]) -> None:
        if payload.get("jsonrpc") != "2.0":
            raise DeviceMcpError("Invalid device MCP JSON-RPC version")
        request_id = payload.get("id")
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            return
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code", -32000)
            message = str(error.get("message", "Device MCP error"))[:256]
            future.set_exception(DeviceMcpError(f"Device MCP error {code}: {message}"))
            return
        result = payload.get("result")
        if not isinstance(result, dict):
            future.set_exception(DeviceMcpError("Device MCP response is missing result"))
            return
        future.set_result(result)

    async def close(self) -> None:
        self._closed = True
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.cancel()

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        context: OperationContext,
    ) -> dict[str, Any]:
        context.checkpoint()
        if self._closed:
            raise DeviceMcpError("Device MCP client is closed")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            await self._sender(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            return await await_operation(future, context)
        finally:
            self._pending.pop(request_id, None)

    @staticmethod
    def _validate_initialize_result(value: dict[str, Any]) -> None:
        server_info = value.get("serverInfo")
        if (
            value.get("protocolVersion") != "2024-11-05"
            or not isinstance(value.get("capabilities"), dict)
            or not isinstance(server_info, dict)
            or not isinstance(server_info.get("name"), str)
            or not isinstance(server_info.get("version"), str)
        ):
            raise DeviceMcpError("Device MCP initialize result is invalid")

    @staticmethod
    def _parse_tool(value: Any) -> DeviceTool:
        if not isinstance(value, dict):
            raise DeviceMcpError("Device MCP tool entry must be an object")
        name = value.get("name")
        description = value.get("description")
        input_schema = value.get("inputSchema")
        if (
            not isinstance(name, str)
            or not name.startswith("self.")
            or len(name) > 128
            or not isinstance(description, str)
            or len(description) > 512
            or not isinstance(input_schema, dict)
        ):
            raise DeviceMcpError("Device MCP tool entry is invalid")
        try:
            Draft202012Validator.check_schema(input_schema)
        except SchemaError as error:
            raise DeviceMcpError("Device MCP tool input schema is invalid") from error
        return DeviceTool(name, description, input_schema)

    @staticmethod
    def _validate_call_result(name: str, result: dict[str, Any]) -> None:
        content = result.get("content")
        is_error = result.get("isError")
        if (
            not isinstance(content, list)
            or len(content) > 32
            or not isinstance(is_error, bool)
        ):
            raise DeviceMcpError(f"Device MCP tool result is invalid: {name}")
        total_text_bytes = 0
        for item in content:
            if (
                not isinstance(item, dict)
                or item.get("type") != "text"
                or not isinstance(item.get("text"), str)
            ):
                raise DeviceMcpError(f"Device MCP tool result is invalid: {name}")
            total_text_bytes += len(item["text"].encode("utf-8"))
            if total_text_bytes > 6_144:
                raise DeviceMcpError(f"Device MCP tool result is too large: {name}")
