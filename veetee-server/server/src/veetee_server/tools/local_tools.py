"""Standard local tool implementations and default registration helper."""

from __future__ import annotations

import datetime
from typing import Any

from veetee_server.tools.executor import ToolContext
from veetee_server.tools.registry import ToolDefinition, ToolRegistry


async def _handle_get_time(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    tz_str = str(args.get("timezone", "UTC")).strip()
    now = datetime.datetime.now(datetime.UTC)
    return {
        "timestamp_iso": now.isoformat(),
        "timezone": tz_str,
        "time_str": now.strftime("%H:%M:%S UTC, %A %B %d %Y"),
    }


async def _handle_get_weather(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    location = str(args.get("location", "Hà Nội")).strip()
    return {
        "location": location,
        "temperature_celsius": 28.5,
        "condition": "Nắng nhẹ",
        "humidity_percent": 65,
    }


async def _handle_search_knowledge(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    return {
        "query": query,
        "results": [
            {
                "title": f"Kết quả cho '{query}'",
                "snippet": f"Đây là thông tin tìm kiếm liên quan tới {query}.",
            }
        ],
    }


async def _handle_control_device(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    action = str(args.get("action", "reboot")).strip()
    return {
        "device_id": context.device_id,
        "action": action,
        "status": "executed",
        "timestamp_iso": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def create_default_local_tools() -> list[ToolDefinition]:
    """Returns standard set of default local tools."""
    return [
        ToolDefinition(
            name="local.get_time",
            description="Lấy giờ hệ thống theo múi giờ chỉ định.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Tên múi giờ, ví dụ Asia/Ho_Chi_Minh hoặc UTC",
                    }
                },
                "required": [],
            },
            version="v1.0.0",
            requires_confirmation=False,
            handler=_handle_get_time,
        ),
        ToolDefinition(
            name="local.get_weather",
            description="Tra cứu thông tin thời tiết tại một địa điểm.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "Tên thành phố/địa điểm"}
                },
                "required": ["location"],
            },
            version="v1.0.0",
            requires_confirmation=False,
            handler=_handle_get_weather,
        ),
        ToolDefinition(
            name="local.search_knowledge",
            description="Tìm kiếm cơ sở tri thức local.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Từ khóa tìm kiếm"}
                },
                "required": ["query"],
            },
            version="v1.0.0",
            requires_confirmation=False,
            handler=_handle_search_knowledge,
        ),
        ToolDefinition(
            name="local.control_device",
            description="Điều khiển thiết bị phần cứng (yêu cầu xác nhận).",
            parameters_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["reboot", "factory_reset", "toggle_power"],
                    }
                },
                "required": ["action"],
            },
            version="v1.0.0",
            requires_confirmation=True,
            handler=_handle_control_device,
        ),
    ]


def register_default_local_tools(registry: ToolRegistry) -> None:
    """Registers all default local tools into the registry."""
    for tool in create_default_local_tools():
        registry.register(tool, override=True)
