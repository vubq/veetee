from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class Intent(str, Enum):
    CHAT = "chat"
    END_CONVERSATION = "end_conversation"
    CLARIFY = "clarify"
    TOOL_REQUEST = "tool_request"
    MEMORY_REMEMBER = "memory_remember"
    MEMORY_FORGET = "memory_forget"
    MEMORY_RECALL = "memory_recall"


@dataclass(frozen=True)
class IntentDecision:
    intent: Intent = Intent.CHAT
    lifecycle: str = "continue"
    emotion: str = "neutral"

    @property
    def should_end(self) -> bool:
        return self.lifecycle == "end" or self.intent == Intent.END_CONVERSATION


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    tool_name: str
    arguments: Dict[str, Any]
    args_hash: str
    session_id: str
    owner_scope: str
    created_at: float
    expires_at: float


def _canonical_args_hash(arguments: Dict[str, Any]) -> str:
    encoded = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_confirmation_text(text: str) -> str:
    return " ".join(
        (text or "").strip().lower().replace(".", " ").replace(",", " ").split()
    )


def confirmation_value(text: str) -> Optional[bool]:
    """Return an explicit local yes/no confirmation, otherwise ``None``.

    This intentionally accepts only short, unambiguous replies. Longer turns
    continue through the normal LLM path and invalidate the pending action so a
    later standalone "ừ" cannot authorize stale arguments.
    """
    normalized = _normalize_confirmation_text(text)
    if normalized in {
        "ừ",
        "ừm",
        "ok",
        "okay",
        "đồng ý",
        "xác nhận",
        "được",
        "được nhé",
        "có",
        "yes",
    }:
        return True
    if normalized in {
        "không",
        "không nhé",
        "thôi",
        "hủy",
        "huỷ",
        "hủy đi",
        "huỷ đi",
        "không đồng ý",
        "no",
    }:
        return False
    return None


class PendingActionStore:
    """One-session confirmation state with TTL and argument binding."""

    def __init__(self):
        self._pending: Optional[PendingAction] = None

    def prepare(
        self,
        *,
        action_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str,
        owner_scope: str,
        ttl_seconds: float,
        now: Optional[float] = None,
    ) -> PendingAction:
        created_at = time.monotonic() if now is None else float(now)
        copied_arguments = dict(arguments)
        pending = PendingAction(
            action_id=str(action_id),
            tool_name=str(tool_name),
            arguments=copied_arguments,
            args_hash=_canonical_args_hash(copied_arguments),
            session_id=str(session_id),
            owner_scope=str(owner_scope),
            created_at=created_at,
            expires_at=created_at + max(0.1, float(ttl_seconds)),
        )
        self._pending = pending
        return pending

    def peek(self, *, now: Optional[float] = None) -> Optional[PendingAction]:
        pending = self._pending
        if pending is None:
            return None
        current = time.monotonic() if now is None else float(now)
        if current > pending.expires_at:
            self._pending = None
            return None
        return pending

    def consume_confirmation(
        self,
        text: str,
        *,
        session_id: str,
        owner_scope: str,
        now: Optional[float] = None,
    ) -> tuple[Optional[bool], Optional[PendingAction]]:
        pending = self.peek(now=now)
        if pending is None:
            return None, None
        if pending.session_id != str(session_id) or pending.owner_scope != str(owner_scope):
            return None, None
        decision = confirmation_value(text)
        if decision is None:
            return None, pending
        self._pending = None
        return decision, pending

    def invalidate_if_changed(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        now: Optional[float] = None,
    ) -> bool:
        pending = self.peek(now=now)
        if pending is None:
            return False
        changed = (
            pending.tool_name != str(tool_name)
            or pending.args_hash != _canonical_args_hash(arguments)
        )
        if changed:
            self._pending = None
        return changed

    def clear(self) -> None:
        self._pending = None


def validate_control(intent: str, lifecycle: str, emotion: str) -> IntentDecision:
    try:
        parsed_intent = Intent(intent)
    except ValueError:
        parsed_intent = Intent.CHAT
    lifecycle = lifecycle if lifecycle in {"continue", "end"} else "continue"
    valid_emotions = {"happy", "neutral", "sad", "surprised", "thinking", "angry", "relaxed"}
    emotion = emotion if emotion in valid_emotions else "neutral"
    if lifecycle == "end":
        parsed_intent = Intent.END_CONVERSATION
    return IntentDecision(parsed_intent, lifecycle, emotion)
