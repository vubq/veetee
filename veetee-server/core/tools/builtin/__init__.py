from core.tools.builtin.calculator import calculator_descriptor
from core.tools.builtin.time_tool import time_descriptor
from core.tools.registry import ToolRegistry


def build_builtin_registry() -> ToolRegistry:
    return ToolRegistry([time_descriptor(), calculator_descriptor()])


__all__ = ["build_builtin_registry"]
