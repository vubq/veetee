from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from core.tools.results import ToolResult


ToolHandler = Callable[[Dict[str, Any]], Union[Any, Awaitable[Any], ToolResult, Awaitable[ToolResult]]]
ToolRenderer = Callable[[ToolResult], str]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler
    timeout_ms: int = 1500
    read_only: bool = True
    idempotent: bool = True
    version: str = "1"
    renderer: Optional[ToolRenderer] = None
    concurrency_group: str = "default"
    requires_confirmation: bool = False
    confirmation_prompt: str = ""

    def as_openai_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
