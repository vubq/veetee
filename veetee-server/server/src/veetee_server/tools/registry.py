"""Tool definition dataclass, policy checks, and collision fail-fast ToolRegistry."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class ToolRegistryError(Exception):
    """Base exception for tool registry errors."""


class ToolCollisionError(ToolRegistryError):
    """Raised when registering a tool whose name conflicts with an existing tool."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool is not found."""


_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Typed tool definition with schema, versioning, and confirmation flag."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    version: str = "v1.0.0"
    requires_confirmation: bool = False
    handler: Callable[[dict[str, Any], Any], Awaitable[Any]] | None = field(
        default=None, repr=False
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"Invalid tool name '{self.name}'. Must match pattern {_NAME_PATTERN.pattern}"
            )


@dataclass
class ToolPolicy:
    """RBAC and device policy defining tool access permissions."""

    allowlist: set[str] | None = None
    denylist: set[str] | None = None
    requires_confirmation_tools: set[str] = field(default_factory=set)

    def is_allowed(self, tool_name: str) -> bool:
        if self.denylist is not None and tool_name in self.denylist:
            return False
        if self.allowlist is not None and tool_name not in self.allowlist:
            return False
        return True

    def requires_confirmation(self, tool_name: str, tool_def: ToolDefinition | None = None) -> bool:
        if tool_name in self.requires_confirmation_tools:
            return True
        if tool_def is not None and tool_def.requires_confirmation:
            return True
        return False


class ToolRegistry:
    """Unified tool registry with namespace support, collision fail-fast, and policy filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self, tool: ToolDefinition, override: bool = False
    ) -> ToolDefinition:
        """Registers a tool definition.

        Raises ToolCollisionError if tool.name is already registered and override is False.
        """
        if tool.name in self._tools and not override:
            raise ToolCollisionError(
                f"Tool name collision: '{tool.name}' is already registered in ToolRegistry."
            )
        self._tools[tool.name] = tool
        return tool

    def get_tool(self, name: str) -> ToolDefinition:
        """Gets tool definition by name. Raises ToolNotFoundError if missing."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry.")
        return self._tools[name]

    def list_tools(
        self, policy: ToolPolicy | None = None
    ) -> list[ToolDefinition]:
        """Lists registered tools filtered by optional ToolPolicy."""
        tools = list(self._tools.values())
        if policy is not None:
            tools = [t for t in tools if policy.is_allowed(t.name)]
        return sorted(tools, key=lambda t: t.name)

    def to_openai_schemas(
        self, policy: ToolPolicy | None = None
    ) -> list[dict[str, Any]]:
        """Exports tools as OpenAI function calling JSON schema specifications."""
        tools = self.list_tools(policy=policy)
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema,
                },
            }
            for t in tools
        ]
