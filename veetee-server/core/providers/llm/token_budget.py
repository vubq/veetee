"""Whole-request token estimation for pre-dispatch quota reservation.

The estimate must cover the complete upstream request (persona, history,
memory, tool schemas/results, control instructions), never just the latest
user sentence. It is deliberately conservative: underestimation causes HTTP
429s, overestimation only leaves headroom unused.

Calibration against ``usage`` actuals is the caller's job (see router).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _content_chars(message: Dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        return len(content)
    try:
        return len(json.dumps(content, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(content))


def estimate_request_tokens(
    messages: List[Dict[str, Any]],
    *,
    tools: Optional[List[Dict[str, Any]]] = None,
    output_budget: int = 0,
    chars_per_token: int = 4,
    margin: float = 1.25,
) -> int:
    """Estimate input + reserved output tokens for one inference request."""
    chars = sum(_content_chars(message) for message in (messages or []))
    if tools:
        try:
            chars += len(json.dumps(tools, ensure_ascii=False))
        except (TypeError, ValueError):
            chars += sum(len(str(tool)) for tool in tools)
    estimated_input = int(chars / max(1, chars_per_token) * margin) + 1
    return estimated_input + max(0, int(output_budget))
