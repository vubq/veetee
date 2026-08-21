"""MCP JSON-RPC adapter implementation with stale session/generation protection and tool binding."""

from __future__ import annotations

import json
from typing import Any

from veetee_server.mcp.protocol import (
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPErrorCode,
)
from veetee_server.tools.executor import (
    ConfirmationRequiredError,
    PolicyViolationError,
    ToolContext,
    ToolExecutor,
)
from veetee_server.tools.registry import (
    ToolNotFoundError,
    ToolPolicy,
    ToolRegistry,
)


class StaleSessionError(Exception):
    """Raised when request refers to a stale session or mismatched generation."""


class MCPAdapter:
    """JSON-RPC 2.0 MCP adapter handling initialize, tools/list (paginated), and tools/call."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor | None = None,
        server_info: dict[str, Any] | None = None,
    ) -> None:
        self.registry = tool_registry
        self.executor = tool_executor or ToolExecutor()
        self.server_info = server_info or {"name": "veetee-mcp-server", "version": "1.0.0"}

    async def handle_request_json(
        self,
        raw_json: str,
        *,
        active_session_id: str = "",
        active_generation_id: str = "",
        policy: ToolPolicy | None = None,
    ) -> str:
        """Processes raw JSON-RPC text payload and returns raw JSON response text."""
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            err_resp = JSONRPCResponse(
                id=None,
                error=JSONRPCError(
                    code=MCPErrorCode.PARSE_ERROR,
                    message=f"JSON parse error: {exc}",
                ),
            )
            return json.dumps(err_resp.to_dict(), ensure_ascii=False)

        resp = await self.handle_request(
            payload,
            active_session_id=active_session_id,
            active_generation_id=active_generation_id,
            policy=policy,
        )
        return json.dumps(resp.to_dict(), ensure_ascii=False)

    async def handle_request(
        self,
        data: dict[str, Any],
        *,
        active_session_id: str = "",
        active_generation_id: str = "",
        policy: ToolPolicy | None = None,
    ) -> JSONRPCResponse:
        """Processes dict payload and returns JSONRPCResponse."""
        try:
            req = JSONRPCRequest.from_dict(data)
        except ValueError as exc:
            return JSONRPCResponse(
                id=data.get("id") if isinstance(data, dict) else None,
                error=JSONRPCError(
                    code=MCPErrorCode.INVALID_REQUEST,
                    message=str(exc),
                ),
            )

        if req.method == "initialize":
            return self._handle_initialize(req)
        elif req.method == "tools/list":
            return self._handle_tools_list(req, policy=policy)
        elif req.method == "tools/call":
            return await self._handle_tools_call(
                req,
                active_session_id=active_session_id,
                active_generation_id=active_generation_id,
                policy=policy,
            )
        else:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.METHOD_NOT_FOUND,
                    message=f"Method '{req.method}' not found.",
                ),
            )

    def _handle_initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(
            id=req.id,
            result={
                "protocolVersion": "2024-11-05",
                "serverInfo": self.server_info,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
            },
        )

    def _handle_tools_list(
        self, req: JSONRPCRequest, policy: ToolPolicy | None = None
    ) -> JSONRPCResponse:
        cursor = req.params.get("cursor")
        limit = int(req.params.get("limit", 50))
        if limit <= 0:
            limit = 50

        tools = self.registry.list_tools(policy=policy)

        # Pagination logic
        start_idx = 0
        if cursor and cursor.isdigit():
            start_idx = int(cursor)

        end_idx = start_idx + limit
        page_tools = tools[start_idx:end_idx]

        next_cursor = str(end_idx) if end_idx < len(tools) else None

        tools_data = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.parameters_schema,
            }
            for t in page_tools
        ]

        return JSONRPCResponse(
            id=req.id,
            result={
                "tools": tools_data,
                "nextCursor": next_cursor,
            },
        )

    async def _handle_tools_call(
        self,
        req: JSONRPCRequest,
        *,
        active_session_id: str,
        active_generation_id: str,
        policy: ToolPolicy | None,
    ) -> JSONRPCResponse:
        name = req.params.get("name")
        if not name or not isinstance(name, str):
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message="Field 'name' is required for tools/call.",
                ),
            )

        arguments = req.params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message="Field 'arguments' must be a dictionary.",
                ),
            )

        # Stale session/generation protection
        req_session_id = req.params.get("session_id", active_session_id)
        req_generation_id = req.params.get("generation_id", active_generation_id)

        if active_session_id and req_session_id != active_session_id:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.STALE_SESSION,
                    message=(
                        f"Session mismatch or stale session: expected '{active_session_id}', "
                        f"got '{req_session_id}'"
                    ),
                ),
            )

        if active_generation_id and req_generation_id != active_generation_id:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.STALE_SESSION,
                    message=(
                        f"Generation mismatch or stale turn: expected '{active_generation_id}', "
                        f"got '{req_generation_id}'"
                    ),
                ),
            )

        # Retrieve tool
        try:
            tool_def = self.registry.get_tool(name)
        except ToolNotFoundError as exc:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.INVALID_PARAMS,
                    message=str(exc),
                ),
            )

        # Build tool context
        context = ToolContext(
            user_id=str(req.params.get("user_id", "anonymous")),
            agent_id=str(req.params.get("agent_id", "default_agent")),
            device_id=str(req.params.get("device_id", "local_device")),
            session_id=req_session_id,
            generation_id=req_generation_id,
            confirmation_granted=bool(req.params.get("confirmation_granted", False)),
        )

        try:
            tool_res = await self.executor.execute(
                tool=tool_def,
                arguments=arguments,
                context=context,
                policy=policy,
            )
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": tool_res.output,
                        }
                    ],
                    "isError": tool_res.status != "success",
                    "metadata": {
                        "duration_ms": tool_res.duration_ms,
                        "truncated": tool_res.truncated,
                    },
                },
            )
        except PolicyViolationError as exc:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.POLICY_VIOLATION,
                    message=str(exc),
                ),
            )
        except ConfirmationRequiredError as exc:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.CONFIRMATION_REQUIRED,
                    message=str(exc),
                ),
            )
        except Exception as exc:
            return JSONRPCResponse(
                id=req.id,
                error=JSONRPCError(
                    code=MCPErrorCode.INTERNAL_ERROR,
                    message=f"Tool execution failed: {exc}",
                ),
            )
