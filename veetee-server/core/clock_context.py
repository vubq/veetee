"""Fresh context data, independent of user-text interpretation."""
import json

from core.tools.builtin.time_tool import clock_snapshot


CLOCK_CONTEXT_PREFIX = "Current-turn server_clock snapshot (authoritative data):\n"


def clock_context(timezone: str) -> dict:
    return {
        "role": "system",
        "content": CLOCK_CONTEXT_PREFIX + json.dumps(
            clock_snapshot(timezone), ensure_ascii=False, separators=(",", ":")
        ),
    }
