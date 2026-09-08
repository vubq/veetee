from __future__ import annotations

import hashlib
import re

from core.memory.models import MemoryProposal


_SECRET_RE = re.compile(r"\b(password|mật khẩu|api\s*key|token|secret|otp|mã xác thực)\b", re.IGNORECASE)


class MemoryPolicy:
    @staticmethod
    def explicit_proposal(text: str) -> MemoryProposal | None:
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            return None
        lower = cleaned.lower()
        if re.search(r"\bquên\s+(?:hết|mọi\s+thứ|tất\s+cả)\b", lower):
            return MemoryProposal(action="forget_all", evidence=cleaned)
        forget = re.search(r"\bquên(?:\s+đi|\s+giúp\s+(?:tôi|mình))?\s+(?:rằng\s+)?(.+)$", cleaned, re.IGNORECASE)
        if forget:
            value = forget.group(1).strip(" .,!?")
            return MemoryProposal(action="forget", value=value, evidence=cleaned)
        remember = re.search(
            r"\bnhớ(?:\s+giúp\s+(?:tôi|mình))?(?:\s+rằng|\s+là)?\s+(.+)$",
            cleaned,
            re.IGNORECASE,
        )
        if remember:
            value = remember.group(1).strip(" .,!?")
            if not value or _SECRET_RE.search(value):
                return None
            key = "fact_" + hashlib.sha256(value.lower().encode("utf-8")).hexdigest()[:16]
            return MemoryProposal(action="upsert", key=key, value=value, evidence=cleaned)
        return None
