from __future__ import annotations

import ast
import math
import operator

from core.tools.base import ToolDescriptor
from core.tools.results import ToolStatus


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node, *, depth=0):
    if depth > 16:
        raise ValueError("expression is too deep")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth=depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, depth=depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval(node.left, depth=depth + 1)
        right = _eval(node.right, depth=depth + 1)
        if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1_000_000):
            raise ValueError("exponent is outside the safe range")
        value = _BINOPS[type(node.op)](left, right)
        if not math.isfinite(float(value)) or abs(float(value)) > 1e15:
            raise ValueError("result is outside the safe range")
        return value
    raise ValueError("unsupported expression")


def _handler(arguments):
    expression = str(arguments.get("expression", "")).strip()
    if not expression or len(expression) > 160:
        raise ValueError("expression must contain 1-160 characters")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 64:
        raise ValueError("expression is too complex")
    return {"expression": expression, "result": _eval(tree)}


def _render(result):
    if result.status != ToolStatus.SUCCEEDED:
        return "Mình chưa tính được biểu thức đó."
    data = result.data or {}
    return f"Kết quả là {data.get('result')}."


def calculator_descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="calculate",
        description="Tính biểu thức số học cơ bản một cách an toàn.",
        input_schema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        handler=_handler,
        renderer=_render,
        timeout_ms=300,
        read_only=True,
        idempotent=True,
        concurrency_group="calculator",
    )
