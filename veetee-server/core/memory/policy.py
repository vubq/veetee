from __future__ import annotations

import hashlib
import re

from core.memory.models import MemoryProposal


_SECRET_RE = re.compile(r"\b(password|mật khẩu|api\s*key|token|secret|otp|mã xác thực)\b", re.IGNORECASE)

_FORGET_ALL_RE = re.compile(
    r"^(?:hãy\s+|hay\s+)?(?:quên|quen)\s+"
    r"(?:hết|het|mọi\s+thứ|moi\s+thu|tất\s+cả|tat\s+ca)"
    r"(?:\s+(?:giúp\s+)?(?:tôi|toi|mình|minh))?\s*[.!?]*$",
    re.IGNORECASE,
)
_FORGET_RE = re.compile(
    r"^(?:hãy\s+|hay\s+)?(?:quên|quen)"
    r"(?:\s+(?:đi|di)|\s+giúp\s+(?:tôi|toi|mình|minh))?"
    r"\s+(?:rằng\s+|rang\s+)?(.+)$",
    re.IGNORECASE,
)
_REMEMBER_RE = re.compile(
    r"^(?:hãy\s+|hay\s+)?(?:nhớ|nho)"
    r"(?:\s+giúp\s+(?:tôi|toi|mình|minh))?"
    r"(?:\s+(?:rằng|rang|là|la))?\s+(.+)$",
    re.IGNORECASE,
)


class MemoryPolicy:
    @staticmethod
    def explicit_proposal(text: str) -> MemoryProposal | None:
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            return None
        if _FORGET_ALL_RE.fullmatch(cleaned):
            return MemoryProposal(action="forget_all", evidence=cleaned)
        forget = _FORGET_RE.fullmatch(cleaned)
        if forget:
            value = forget.group(1).strip(" .,!?")
            if not value:
                return None
            return MemoryProposal(action="forget", value=value, evidence=cleaned)
        remember = _REMEMBER_RE.fullmatch(cleaned)
        if remember:
            value = remember.group(1).strip(" .,!?")
            if not value or _SECRET_RE.search(value):
                return None
            key = "fact_" + hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:16]
            return MemoryProposal(action="upsert", key=key, value=value, evidence=cleaned)
        return None
