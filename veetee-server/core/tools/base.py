from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Union

from core.tools.results import ToolResult


ToolHandler = Callable[[Dict[str, Any]], Union[Any, Awaitable[Any], ToolResult, Awaitable[ToolResult]]]
READ_ONLY_TOOL_DESCRIPTION_MARKER = "Tool chỉ đọc:"


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
    concurrency_group: str = "default"
    requires_confirmation: bool = False

    def as_openai_tool(self) -> Dict[str, Any]:
        description = self.description
        if self.read_only:
            description += (
                f" {READ_ONLY_TOOL_DESCRIPTION_MARKER} nếu cần dữ liệu này để trả lời người dùng, hãy gọi ngay trong cùng lượt; "
                "không cần xin xác nhận và không kết thúc bằng câu chờ trước khi gọi tool."
            )
        elif self.requires_confirmation:
            description += (
                " Action này có thể cần xác nhận; hãy phát tool call khi người dùng yêu cầu, "
                "server sẽ quản lý bước xác nhận trước khi thực thi."
            )
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": self.input_schema,
            },
        }
