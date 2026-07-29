from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from veetee_voice_server.conversation.cancellation import OperationContext, await_operation
from veetee_voice_server.providers.contracts import ToolBroker

ToolHandler = Callable[[dict[str, Any], OperationContext], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    audience: str = "regular"
    safety_class: str = "read_only"
    requires_confirmation: bool = False


class RegistryToolBroker:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self._validate_tool(tool)
            if tool.name in self._tools:
                raise ValueError(f"Duplicate server MCP tool: {tool.name}")
            self._tools[tool.name] = tool

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "audience": tool.audience,
                "safetyClass": tool.safety_class,
                "requiresConfirmation": tool.requires_confirmation,
            }
            for tool in self._tools.values()
        ]

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: OperationContext,
    ) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        context.checkpoint()
        validation_error = next(
            Draft202012Validator(tool.input_schema).iter_errors(arguments), None
        )
        if validation_error is not None:
            raise ValueError(
                f"Invalid server MCP arguments for {name}: "
                f"{validation_error.message[:256]}"
            )
        result = await await_operation(tool.handler(arguments, context), context)
        context.checkpoint()
        return {
            "tool": name,
            "arguments": arguments,
            "result": result,
        }

    @staticmethod
    def _validate_tool(tool: ToolSpec) -> None:
        if (
            not tool.name
            or len(tool.name) > 128
            or not tool.description
            or len(tool.description) > 512
            or tool.audience not in {"regular", "user"}
            or tool.safety_class
            not in {"read_only", "reversible", "disruptive", "destructive"}
            or (tool.audience == "user" and not tool.requires_confirmation)
        ):
            raise ValueError(f"Invalid server MCP tool: {tool.name}")
        try:
            Draft202012Validator.check_schema(tool.input_schema)
        except SchemaError as error:
            raise ValueError(f"Invalid server MCP schema: {tool.name}") from error


class CompositeToolBroker:
    """Merge bounded brokers while preserving each broker's call policy."""

    def __init__(self, *brokers: ToolBroker) -> None:
        self._brokers = tuple(brokers)

    def list_tools(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        seen: set[str] = set()
        for broker in self._brokers:
            for item in broker.list_tools():
                if not isinstance(item, dict):
                    raise ValueError("Composite MCP catalog contains a non-object tool")
                name = item.get("name")
                if not isinstance(name, str) or not name or len(name) > 128:
                    raise ValueError("Composite MCP catalog contains an invalid tool")
                if name in seen:
                    raise ValueError(f"Duplicate composite MCP tool: {name}")
                seen.add(name)
                catalog.append(item)
                if len(catalog) > 128:
                    raise ValueError("Composite MCP catalog exceeds 128 tools")
        return catalog

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: OperationContext,
    ) -> Any:
        context.checkpoint()
        # Validate every catalog before dispatch so malformed downstream tools
        # cannot bypass the composite duplicate/size policy.
        self.list_tools()
        owner: ToolBroker | None = None
        for broker in self._brokers:
            if any(
                item.get("name") == name
                for item in broker.list_tools()
                if isinstance(item, dict)
            ):
                if owner is not None:
                    raise ValueError(f"Duplicate composite MCP tool: {name}")
                owner = broker
        if owner is None:
            raise KeyError(f"Unknown MCP tool: {name}")
        return await owner.call(name, arguments, context)
