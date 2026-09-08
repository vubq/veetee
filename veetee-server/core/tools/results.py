from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    status: ToolStatus
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.SUCCEEDED
