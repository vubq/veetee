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
            "Lấy sự thật về ngày/giờ hiện tại theo múi giờ IANA. Dùng tool khi câu trả lời "
            "thực sự cần biết đồng hồ hoặc ngày hiện tại; đừng gọi chỉ vì người dùng nhắc tới "
            "một mốc giờ, lịch trình hay hỏi giờ khuyến nghị. Nếu cần ngày/giờ hiện tại mà không "
            f"có múi giờ cụ thể thì dùng mặc định {default_timezone}. "
            "Nếu context lượt hiện tại đã có server_clock đúng múi giờ và đủ dữ liệu, "
            "dùng trực tiếp snapshot đó; không cần gọi lại tool."
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
