from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.tools.base import ToolDescriptor
from core.tools.results import ToolStatus


def _handler(arguments):
    timezone = arguments.get("timezone", "Asia/Bangkok")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc
    now = datetime.now(zone)
    return {
        "timezone": timezone,
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    }


def _render(result):
    if result.status != ToolStatus.SUCCEEDED:
        return "Mình chưa lấy được giờ hiện tại."
    data = result.data or {}
    return f"Bây giờ là {data.get('time', '')}, ngày {data.get('date', '')} ({data.get('timezone', '')})."


def time_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="get_current_time",
        description="Lấy ngày giờ hiện tại theo múi giờ IANA, ví dụ Asia/Bangkok.",
        input_schema={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "required": ["timezone"],
            "additionalProperties": False,
        },
        handler=_handler,
        renderer=_render,
        timeout_ms=300,
        read_only=True,
        idempotent=True,
        concurrency_group="clock",
    )
