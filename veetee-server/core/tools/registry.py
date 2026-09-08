from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from core.tools.base import ToolDescriptor


_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolValidationError(ValueError):
    pass


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return type(value) is bool
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def validate_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ToolValidationError("tool arguments must be an object")
    if schema.get("type", "object") != "object":
        raise ToolValidationError("top-level tool schema must be an object")
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    missing = sorted(required - set(arguments))
    if missing:
        raise ToolValidationError("missing required argument(s): " + ", ".join(missing))
    if schema.get("additionalProperties") is False:
        extras = sorted(set(arguments) - set(properties))
        if extras:
            raise ToolValidationError("unknown argument(s): " + ", ".join(extras))
    for name, value in arguments.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            continue
        expected = rule.get("type")
        if expected and not _matches_type(value, expected):
            raise ToolValidationError(f"argument {name!r} must be {expected}")
        if "enum" in rule and value not in rule["enum"]:
            raise ToolValidationError(f"argument {name!r} is outside the allowed values")
        if isinstance(value, str):
            if len(value) < int(rule.get("minLength", 0)):
                raise ToolValidationError(f"argument {name!r} is too short")
            if len(value) > int(rule.get("maxLength", 1_000_000)):
                raise ToolValidationError(f"argument {name!r} is too long")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                raise ToolValidationError(f"argument {name!r} is below minimum")
            if "maximum" in rule and value > rule["maximum"]:
                raise ToolValidationError(f"argument {name!r} is above maximum")


class ToolRegistry:
    def __init__(self, descriptors: Iterable[ToolDescriptor] = ()):
        self._tools: Dict[str, ToolDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor, *, replace: bool = False) -> None:
        if not _TOOL_NAME_RE.fullmatch(descriptor.name):
            raise ValueError(f"invalid tool name: {descriptor.name!r}")
        if descriptor.name in self._tools and not replace:
            raise ValueError(f"tool already registered: {descriptor.name}")
        self._tools[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def descriptors(self) -> List[ToolDescriptor]:
        return list(self._tools.values())

    def openai_tools(self, *, limit: int = 16) -> List[Dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self.descriptors()[: max(0, int(limit))]]

    def clone(self) -> "ToolRegistry":
        return ToolRegistry(self.descriptors())
