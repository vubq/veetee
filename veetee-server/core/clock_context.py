"""Fresh context data, independent of user-text interpretation."""
import json

from core.tools.builtin.time_tool import clock_snapshot


CLOCK_CONTEXT_PREFIX = "Current-turn server_clock snapshot (authoritative data):\n"

_WEEKDAYS_VI = {
    1: "Thứ Hai",
    2: "Thứ Ba",
    3: "Thứ Tư",
    4: "Thứ Năm",
    5: "Thứ Sáu",
    6: "Thứ Bảy",
    7: "Chủ Nhật",
}


def clock_context(timezone: str) -> dict:
    snapshot = clock_snapshot(timezone)
    try:
        hour, minute, _second = snapshot["time"].split(":")
        year, month, day = snapshot["date"].split("-")
        weekday = _WEEKDAYS_VI.get(int(snapshot["weekday_iso"]), "")
        reading = (
            f"ĐỒNG HỒ CHUẨN (múi giờ {snapshot['timezone']}): bây giờ là "
            f"{int(hour)} giờ {int(minute)} phút, {weekday}, ngày {int(day)}/"
            f"{int(month)}/{year}. Khi được hỏi giờ/ngày/thứ HIỆN TẠI, đọc "
            f"đúng các con số này; cấm đoán, cấm bịa, cấm lấy giờ từ "
            f"persona/ví dụ/lượt cũ."
        )
    except (KeyError, ValueError, AttributeError):
        reading = ""
    content = CLOCK_CONTEXT_PREFIX + json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":")
    )
    if reading:
        content += "\n" + reading
    return {"role": "system", "content": content}
