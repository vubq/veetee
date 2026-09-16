from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.tools.base import ToolDescriptor


def clock_snapshot(timezone: str = "Asia/Bangkok") -> dict:
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
        "weekday_iso": now.isoweekday(),
        "weekday": now.strftime("%A"),
        "observed_at": now.isoformat(timespec="seconds"),
        "source": "server_clock",
    }


def _handler(arguments):
    return clock_snapshot(arguments.get("timezone", "Asia/Bangkok"))


def time_descriptor(default_timezone: str = "Asia/Bangkok") -> ToolDescriptor:
    return ToolDescriptor(
        name="get_current_time",
        description=(
            "Lấy ngày giờ hiện tại theo timezone (mặc định server). "
            "Context mỗi lượt đã có sẵn server_clock; chỉ gọi khi cần timezone khác."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "additionalProperties": False,
        },
        handler=lambda arguments: clock_snapshot(arguments.get("timezone", default_timezone)),
        timeout_ms=300,
        read_only=True,
        idempotent=True,
        concurrency_group="clock",
    )
