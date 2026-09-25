from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import DEFAULT_TIMEZONE
from core.tools.base import ToolDescriptor


_WEEKDAYS_VI = {
    1: "Thứ Hai",
    2: "Thứ Ba",
    3: "Thứ Tư",
    4: "Thứ Năm",
    5: "Thứ Sáu",
    6: "Thứ Bảy",
    7: "Chủ Nhật",
}


def clock_snapshot(timezone: str = DEFAULT_TIMEZONE) -> dict:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc
    now = datetime.now(zone)
    weekday_iso = now.isoweekday()
    return {
        "timezone": timezone,
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday_iso": weekday_iso,
        "weekday": now.strftime("%A"),
        "weekday_vi": _WEEKDAYS_VI[weekday_iso],
        "observed_at": now.isoformat(timespec="seconds"),
        "source": "server_clock",
    }


def time_descriptor(default_timezone: str = DEFAULT_TIMEZONE) -> ToolDescriptor:
    def _handler(arguments: dict) -> dict:
        timezone = str(arguments.get("timezone") or "").strip()
        if not timezone:
            raise ValueError("timezone is required")
        if timezone == default_timezone:
            raise ValueError(
                "server timezone is already available in server_clock; use it directly"
            )
        return clock_snapshot(timezone)

    return ToolDescriptor(
        name="get_time_in_timezone",
        description=(
            "Chỉ dùng khi người dùng hỏi giờ/ngày ở MỘT timezone khác timezone server. "
            "Không dùng cho giờ/ngày hiện tại của server_clock."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "required": ["timezone"],
            "additionalProperties": False,
        },
        handler=_handler,
        timeout_ms=300,
        read_only=True,
        idempotent=True,
        concurrency_group="clock",
    )
