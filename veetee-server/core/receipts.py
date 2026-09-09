from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

MAX_RECEIPT_ARG_CHARS = 2048
MAX_RECEIPT_DATA_CHARS = 4096


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _bounded_json(value: Any, limit: int) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(encoded) <= limit:
        return value
    text = _truncate_text(encoded, limit)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"truncated": True, "chars": len(encoded)}


def make_receipt(
    *,
    call_id: str,
    name: str,
    arguments: Dict[str, Any],
    status: str,
    turn_id: str,
    data: Any = None,
    error: Optional[str] = None,
    changed: Optional[bool] = None,
    execution: Optional[Dict[str, Any]] = None,
    provenance: Optional[str] = None,
    observed_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Standard business/memory/confirmation receipt envelope.

    The envelope never guesses: ``unknown`` stays unknown and awaiting
    confirmation is distinct from approved and from execution succeeded.
    """
    bounded_args = _bounded_json(dict(arguments or {}), MAX_RECEIPT_ARG_CHARS)
    bounded_data = _bounded_json(data, MAX_RECEIPT_DATA_CHARS) if data is not None else None
    receipt: Dict[str, Any] = {
        "id": str(call_id or ""),
        "origin_turn": str(turn_id or ""),
        "name": str(name or ""),
        "arguments": bounded_args,
        "status": str(status or "unknown"),
        "observed_at": float(observed_at) if observed_at is not None else time.time(),
        "provenance": str(provenance or f"tool:{name}"),
    }
    if bounded_data is not None:
        receipt["data"] = bounded_data
    if error:
        receipt["error"] = _truncate_text(str(error), 1000)
    if changed is not None:
        receipt["changed"] = bool(changed)
    if execution is not None:
        receipt["execution"] = {
            "status": str(execution.get("status") or "unknown"),
            "data": _bounded_json(execution.get("data"), MAX_RECEIPT_DATA_CHARS)
            if execution.get("data") is not None
            else None,
            "error": _truncate_text(str(execution.get("error") or ""), 1000)
            if execution.get("error")
            else None,
        }
    return receipt
