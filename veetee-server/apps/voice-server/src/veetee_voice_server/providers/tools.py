from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from veetee_voice_server.conversation.cancellation import OperationContext

ToolHandler = Callable[[dict[str, Any], OperationContext], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    requires_confirmation: bool = False


class RegistryToolBroker:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools = {tool.name: tool for tool in tools or []}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "inputSchema": tool.input_schema,
                "requiresConfirmation": tool.requires_confirmation,
            }
            for tool in self._tools.values()
        ]

    async def call(self, name: str, arguments: dict[str, Any], context: OperationContext) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        context.checkpoint()
        return await tool.handler(arguments, context)
