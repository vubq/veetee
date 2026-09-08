from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MemoryFact:
    id: int
    owner_id: str
    scope: str
    kind: str
    key: str
    value: str
    source_turn_id: str
    evidence: str
    revision: int
    deleted: bool = False
    created_at: str = ""
    updated_at: str = ""
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class MemoryProposal:
    action: str
    value: str = ""
    key: str = ""
    evidence: str = ""
