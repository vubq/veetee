from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.tools.base import ToolDescriptor
from core.tools.registry import ToolRegistry
from core.tools.results import ToolResult, ToolStatus


logger = logging.getLogger("MCPDevice")

MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_LIST_PAGES = 8
MAX_DISCOVERED_TOOLS = 64
MAX_CATALOG_BYTES = 128 * 1024


class MCPUnavailableError(RuntimeError):
    pass


@dataclass
class _PendingRequest:
    generation: int
    future: asyncio.Future


@dataclass(frozen=True)
class _SafeTool:
    exposed_name: str
    read_only: bool
    idempotent: bool
    timeout_ms: int


SAFE_DEVICE_TOOLS: Dict[str, _SafeTool] = {
    "self.get_device_status": _SafeTool(
        exposed_name="device_get_status",
        read_only=True,
        idempotent=True,
        timeout_ms=1200,
    ),
    "self.audio_speaker.set_volume": _SafeTool(
        exposed_name="device_set_volume",
        read_only=False,
        idempotent=True,
        timeout_ms=1200,
    ),
}


def _extract_error(error: Any) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return str(error or "MCP request failed")


def _extract_result_data(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list):
        return result
    texts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if text is not None:
                texts.append(str(text))
    if len(texts) == 1:
        value = texts[0]
        try:
            return json.loads(value)
        except Exception:
            return value
    if texts:
        return "\n".join(texts)
    return result


def _render_status(result: ToolResult) -> str:
    if not result.ok:
        if result.status == ToolStatus.TIMED_OUT:
            return "Mình chưa nhận được trạng thái thiết bị kịp thời."
        return "Mình chưa lấy được trạng thái thiết bị."
    data = result.data
    if isinstance(data, dict):
        return "Trạng thái thiết bị hiện tại: " + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"Trạng thái thiết bị hiện tại: {data}."


def _render_volume(result: ToolResult) -> str:
    if result.status == ToolStatus.UNKNOWN:
        return "Mình đã gửi yêu cầu đổi âm lượng nhưng chưa xác minh được trạng thái cuối cùng."
    if not result.ok:
        return "Mình chưa đổi được âm lượng của thiết bị."
    return "Thiết bị đã phản hồi thành công cho yêu cầu đổi âm lượng."


class MCPDeviceClient:
    """Stock Xiaozhi MCP client bound to one WebSocket session generation."""

    def __init__(
        self,
        *,
        send_payload: Callable[[Dict[str, Any]], Awaitable[bool]],
        registry: ToolRegistry,
        request_timeout_ms: int = 1500,
    ):
        self._send_payload = send_payload
        self.registry = registry
        self.request_timeout_ms = max(100, int(request_timeout_ms))
        self._generation = 0
        self._enabled = False
        self._ready = False
        self._next_request_id = 1
        self._pending: Dict[int, _PendingRequest] = {}
        self._registered_names: set[str] = set()
        self._discovery_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._request_count = 0
        self._catalog_count = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def ready(self) -> bool:
        return self._ready

    async def on_hello(self, supported: bool) -> None:
        self._reset_generation()
        self._enabled = bool(supported)
        if not self._enabled:
            return
        self._discovery_task = asyncio.create_task(self._discover())

    def _reset_generation(self) -> None:
        self._generation += 1
        self._enabled = False
        self._ready = False
        self._last_error = None
        task = self._discovery_task
        if task is not None and not task.done():
            task.cancel()
        self._discovery_task = None
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(MCPUnavailableError("MCP connection generation changed"))
        self._pending.clear()
        for name in self._registered_names:
            self.registry.unregister(name)
        self._registered_names.clear()
        self._catalog_count = 0

    async def close(self) -> None:
        self._reset_generation()

    async def handle_message(self, wrapper: Dict[str, Any]) -> bool:
        if not self._enabled:
            return False
        payload = wrapper.get("payload")
        if not isinstance(payload, dict):
            return False
        request_id = payload.get("id")
        # Notifications deliberately have no ID and never create/complete a waiter.
        if isinstance(request_id, bool) or not isinstance(request_id, int):
            return False
        pending = self._pending.pop(request_id, None)
        if pending is None or pending.generation != self._generation:
            logger.debug("Ignoring stale/unknown MCP response id=%r", request_id)
            return False
        if not pending.future.done():
            pending.future.set_result(payload)
        return True

    async def _request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        if not self._enabled:
            raise MCPUnavailableError("MCP is unavailable for this client")
        generation = self._generation
        request_id = self._next_request_id
        self._next_request_id += 1
        if self._next_request_id > 2_000_000_000:
            self._next_request_id = 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = _PendingRequest(generation=generation, future=future)
        request: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params
        self._request_count += 1
        if not await self._send_payload(request):
            self._pending.pop(request_id, None)
            raise ConnectionError("failed to send MCP request")
        timeout = max(0.1, (timeout_ms or self.request_timeout_ms) / 1000.0)
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise
        finally:
            current = self._pending.get(request_id)
            if current is not None and current.future is future:
                self._pending.pop(request_id, None)
        if generation != self._generation:
            raise MCPUnavailableError("stale MCP response")
        return response

    async def _discover(self) -> None:
        generation = self._generation
        try:
            init = await self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "VeeTee", "version": "1"},
                },
            )
            if "error" in init:
                raise RuntimeError(_extract_error(init.get("error")))
            init_result = init.get("result")
            if not isinstance(init_result, dict):
                raise RuntimeError("invalid MCP initialize result")
            if init_result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
                raise RuntimeError(
                    f"unsupported MCP protocol version: {init_result.get('protocolVersion')!r}"
                )

            tools: list[Dict[str, Any]] = []
            seen_cursors: set[str] = set()
            cursor: Optional[str] = None
            total_bytes = 0
            for _ in range(MAX_LIST_PAGES):
                params: Dict[str, Any] = {"withUserTools": False}
                if cursor:
                    params["cursor"] = cursor
                page = await self._request("tools/list", params)
                if "error" in page:
                    raise RuntimeError(_extract_error(page.get("error")))
                result = page.get("result")
                if not isinstance(result, dict) or not isinstance(result.get("tools", []), list):
                    raise RuntimeError("invalid MCP tools/list result")
                encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                total_bytes += len(encoded)
                if total_bytes > MAX_CATALOG_BYTES:
                    raise RuntimeError("MCP catalog exceeds size limit")
                for item in result.get("tools", []):
                    if not isinstance(item, dict):
                        continue
                    tools.append(item)
                    if len(tools) >= MAX_DISCOVERED_TOOLS:
                        break
                if len(tools) >= MAX_DISCOVERED_TOOLS:
                    break
                next_cursor = result.get("nextCursor")
                if not next_cursor:
                    cursor = None
                    break
                if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                    raise RuntimeError("invalid/repeated MCP nextCursor")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                if cursor:
                    raise RuntimeError("MCP tools/list exceeded page limit")

            if generation != self._generation or not self._enabled:
                return
            self._catalog_count = len(tools)
            self._install_safe_tools(tools)
            self._ready = True
            logger.info(
                "MCP device discovery ready generation=%d catalog=%d exposed=%d",
                generation,
                len(tools),
                len(self._registered_names),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._generation:
                self._last_error = str(exc)
                self._ready = False
                logger.warning("MCP device discovery failed: %s", exc)

    def _install_safe_tools(self, tools: list[Dict[str, Any]]) -> None:
        for item in tools:
            mcp_name = item.get("name")
            safe = SAFE_DEVICE_TOOLS.get(str(mcp_name))
            if safe is None:
                continue
            schema = item.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type", "object") != "object":
                logger.warning("Skipping MCP tool with invalid schema: %s", mcp_name)
                continue
            description = str(item.get("description") or mcp_name)
            renderer = _render_status if mcp_name == "self.get_device_status" else _render_volume

            async def handler(
                arguments: Dict[str, Any],
                *,
                _mcp_name=str(mcp_name),
                _safe=safe,
            ) -> ToolResult:
                return await self._call_device_tool(_mcp_name, _safe, arguments)

            descriptor = ToolDescriptor(
                name=safe.exposed_name,
                description=description,
                input_schema=schema,
                handler=handler,
                timeout_ms=safe.timeout_ms + 100,
                read_only=safe.read_only,
                idempotent=safe.idempotent,
                renderer=renderer,
                concurrency_group="device_mcp",
                requires_confirmation=(safe.exposed_name == "device_set_volume"),
                confirmation_prompt=(
                    "Bạn có muốn đặt âm lượng thiết bị thành {volume}% không?"
                    if safe.exposed_name == "device_set_volume"
                    else ""
                ),
            )
            self.registry.register(descriptor, replace=True)
            self._registered_names.add(safe.exposed_name)

    async def _call_device_tool(
        self,
        mcp_name: str,
        safe: _SafeTool,
        arguments: Dict[str, Any],
    ) -> ToolResult:
        try:
            response = await self._request(
                "tools/call",
                {"name": mcp_name, "arguments": arguments},
                timeout_ms=safe.timeout_ms,
            )
        except asyncio.TimeoutError:
            status = ToolStatus.TIMED_OUT if safe.read_only else ToolStatus.UNKNOWN
            return ToolResult(
                "",
                safe.exposed_name,
                status,
                error="device MCP call timed out",
                metadata={"dispatched": True, "mcp_name": mcp_name},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(
                "",
                safe.exposed_name,
                ToolStatus.FAILED,
                error=str(exc),
                metadata={"mcp_name": mcp_name},
            )

        if "error" in response:
            return ToolResult(
                "",
                safe.exposed_name,
                ToolStatus.FAILED,
                error=_extract_error(response.get("error")),
                metadata={"mcp_name": mcp_name},
            )
        result = response.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            return ToolResult(
                "",
                safe.exposed_name,
                ToolStatus.FAILED,
                error=str(_extract_result_data(result) or "device reported tool error"),
                metadata={"mcp_name": mcp_name},
            )
        return ToolResult(
            "",
            safe.exposed_name,
            ToolStatus.SUCCEEDED,
            data=_extract_result_data(result),
            metadata={"mcp_name": mcp_name},
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "ready": self._ready,
            "generation": self._generation,
            "catalog_count": self._catalog_count,
            "exposed_count": len(self._registered_names),
            "pending_requests": len(self._pending),
            "request_count": self._request_count,
            "last_error": self._last_error,
        }
