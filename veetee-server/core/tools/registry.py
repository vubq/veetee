from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from core.tools.base import ToolDescriptor


_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolValidationError(ValueError):
    pass


MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_BYTES = 16 * 1024
MAX_ARGS_BYTES = 8 * 1024


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
    if expected == "null":
        return value is None
    return True


def _validate_value(schema: Any, value: Any, *, path: str, depth: int) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ToolValidationError(f"{path or 'arguments'} exceeds max schema depth")
    if not isinstance(schema, dict):
        raise ToolValidationError(f"{path or 'arguments'} has an invalid schema")
    # Combinators first so nested branches share the same strictness.
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if options is not None:
            if not isinstance(options, list) or not options:
                raise ToolValidationError(f"{path or 'arguments'} has invalid {key}")
            if key == "anyOf":
                errors = []
                for option in options:
                    try:
                        _validate_value(option, value, path=path, depth=depth + 1)
                        break
                    except ToolValidationError as exc:
                        errors.append(str(exc))
                else:
                    raise ToolValidationError(
                        f"{path or 'arguments'} matches no {key} branch: {errors[0] if errors else 'no branches'}"
                    )
                return
            matches = 0
            last_error = ""
            for option in options:
                try:
                    _validate_value(option, value, path=path, depth=depth + 1)
                    matches += 1
                except ToolValidationError as exc:
                    last_error = str(exc)
            if matches != 1:
                raise ToolValidationError(
                    f"{path or 'arguments'} must match exactly one {key} branch ({last_error or 'ambiguous'})"
                )
            return
    all_of = schema.get("allOf")
    if all_of is not None:
        if not isinstance(all_of, list) or not all_of:
            raise ToolValidationError(f"{path or 'arguments'} has invalid allOf")
        for option in all_of:
            _validate_value(option, value, path=path, depth=depth + 1)
        return
    not_schema = schema.get("not")
    if not_schema is not None:
        try:
            _validate_value(not_schema, value, path=path, depth=depth + 1)
        except ToolValidationError:
            pass
        else:
            raise ToolValidationError(f"{path or 'arguments'} matches a forbidden schema")
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_matches_type(value, item) for item in expected):
            raise ToolValidationError(f"{path or 'arguments'} has an unexpected type")
    elif isinstance(expected, str):
        if not _matches_type(value, expected):
            raise ToolValidationError(f"{path or 'arguments'} must be {expected}")
    elif expected is not None:
        raise ToolValidationError(f"{path or 'arguments'} has an invalid type")
    if "const" in schema and value != schema["const"]:
        raise ToolValidationError(f"{path or 'arguments'} does not match const")
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list) or value not in allowed:
            raise ToolValidationError(f"{path or 'arguments'} is outside the allowed values")
    if isinstance(value, str):
        min_length = int(schema.get("minLength", 0))
        max_length = int(schema.get("maxLength", 1_000_000))
        if len(value) < min_length:
            raise ToolValidationError(f"{path or 'arguments'} is too short")
        if len(value) > max_length:
            raise ToolValidationError(f"{path or 'arguments'} is too long")
        pattern = schema.get("pattern")
        if pattern is not None:
            import re as _re

            try:
                if not _re.search(str(pattern), value):
                    raise ToolValidationError(f"{path or 'arguments'} does not match pattern")
            except _re.error as exc:
                raise ToolValidationError(f"{path or 'arguments'} has invalid pattern: {exc}") from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolValidationError(f"{path or 'arguments'} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolValidationError(f"{path or 'arguments'} is above maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ToolValidationError(f"{path or 'arguments'} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ToolValidationError(f"{path or 'arguments'} has too many items")
        if schema.get("uniqueItems") is True and len({repr(item) for item in value}) != len(value):
            raise ToolValidationError(f"{path or 'arguments'} must contain unique items")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(items, item, path=f"{path}[{index}]", depth=depth + 1)
        elif isinstance(items, list):
            for index, (sub_schema, item) in enumerate(zip(items, value)):
                _validate_value(sub_schema, item, path=f"{path}[{index}]", depth=depth + 1)
            additional = schema.get("additionalItems")
            if additional is False and len(value) > len(items):
                raise ToolValidationError(f"{path or 'arguments'} has too many items")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        if not isinstance(required, list):
            raise ToolValidationError(f"{path or 'arguments'} has invalid required list")
        missing = sorted(name for name in required if name not in value)
        if missing:
            raise ToolValidationError("missing required argument(s): " + ", ".join(missing))
        additional = schema.get("additionalProperties")
        if additional is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ToolValidationError("unknown argument(s): " + ", ".join(extras))
        elif isinstance(additional, dict):
            for name, item in value.items():
                if name not in properties:
                    _validate_value(additional, item, path=f"{path}.{name}" if path else str(name), depth=depth + 1)
        for name, item in value.items():
            rule = properties.get(name)
            if isinstance(rule, dict):
                child = f"{path}.{name}" if path else str(name)
                _validate_value(rule, item, path=child, depth=depth + 1)


def _check_schema_shape(schema: Any, *, depth: int, seen_bytes: list) -> None:
    import json as _json

    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError("tool schema exceeds max depth")
    if not isinstance(schema, dict):
        raise ValueError("tool schema must be an object")
    encoded = _json.dumps(schema, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise ValueError("tool schema exceeds max bytes")
    for key in ("properties",):
        props = schema.get(key)
        if props is None:
            continue
        if not isinstance(props, dict):
            raise ValueError(f"tool schema {key} must be an object")
        for sub in props.values():
            _check_schema_shape(sub, depth=depth + 1, seen_bytes=seen_bytes)
    for key in ("items", "additionalProperties", "not"):
        sub = schema.get(key)
        if isinstance(sub, dict):
            _check_schema_shape(sub, depth=depth + 1, seen_bytes=seen_bytes)
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key)
        if options is None:
            continue
        if not isinstance(options, list) or not options:
            raise ValueError(f"tool schema {key} must be a non-empty list")
        for sub in options:
            _check_schema_shape(sub, depth=depth + 1, seen_bytes=seen_bytes)


def validate_schema(schema: Dict[str, Any]) -> str:
    """Check a tool schema at registration time and return its version hash."""
    import hashlib as _hashlib
    import json as _json

    if not isinstance(schema, dict):
        raise ValueError("tool schema must be an object")
    if schema.get("type", "object") != "object":
        raise ValueError("top-level tool schema must be an object")
    _check_schema_shape(schema, depth=0, seen_bytes=[])
    encoded = _json.dumps(schema, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return _hashlib.sha256(encoded).hexdigest()[:16]


def validate_arguments(schema: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    import json as _json

    if not isinstance(arguments, dict):
        raise ToolValidationError("tool arguments must be an object")
    try:
        encoded = _json.dumps(arguments, ensure_ascii=False, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(f"tool arguments are not serializable: {exc}") from exc
    if len(encoded) > MAX_ARGS_BYTES:
        raise ToolValidationError("tool arguments exceed max bytes")
    # Unknown top-level types default to strict: only known keywords validate.
    _validate_value(schema, arguments, path="", depth=0)


class ToolRegistry:
    def __init__(self, descriptors: Iterable[ToolDescriptor] = ()):
        self._tools: Dict[str, ToolDescriptor] = {}
        self._schema_hashes: Dict[str, str] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor, *, replace: bool = False) -> None:
        if not _TOOL_NAME_RE.fullmatch(descriptor.name):
            raise ValueError(f"invalid tool name: {descriptor.name!r}")
        if descriptor.name in self._tools and not replace:
            raise ValueError(f"tool already registered: {descriptor.name}")
        schema_hash = validate_schema(descriptor.input_schema)
        self._tools[descriptor.name] = descriptor
        self._schema_hashes[descriptor.name] = schema_hash

    def schema_hash(self, name: str) -> str:
        return self._schema_hashes.get(name, "")

    def catalog_fingerprint(self) -> str:
        import hashlib as _hashlib
        import json as _json

        payload = _json.dumps(
            {name: self._schema_hashes.get(name, "") for name in sorted(self._tools)},
            sort_keys=True,
        ).encode("utf-8")
        return _hashlib.sha256(payload).hexdigest()[:16]

    def search(self, query: str, *, limit: int = 8) -> List[ToolDescriptor]:
        """Discovery helper for large catalogs: lexical match on name/description."""
        needle = str(query or "").strip().lower()
        descriptors = self.descriptors()
        if not needle:
            return descriptors[: max(0, int(limit))]
        scored = []
        for item in descriptors:
            haystack = f"{item.name} {item.description}".lower()
            score = sum(1 for token in needle.split() if token and token in haystack)
            if score > 0 or needle in item.name.lower():
                scored.append((score, item.name, item))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [item for _, _, item in scored[: max(0, int(limit))]]

    def get(self, name: str) -> ToolDescriptor | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def descriptors(self) -> List[ToolDescriptor]:
        return list(self._tools.values())

    def openai_tools(self, *, limit: int = 16) -> List[Dict[str, Any]]:
        descriptors = self.descriptors()
        bounded = max(0, int(limit))
        if len(descriptors) <= bounded:
            return [tool.as_openai_tool() for tool in descriptors]
        # Never cut silently: expose the bounded prefix plus an explicit
        # catalog notice so the model can discover the omitted tools via
        # search instead of assuming they do not exist.
        exposed = [tool.as_openai_tool() for tool in descriptors[:bounded]]
        omitted = [tool.name for tool in descriptors[bounded:]]
        exposed.append({
            "type": "function",
            "function": {
                "name": "veetee_tool_catalog",
                "description": (
                    "Danh mục tool discovery do server cung cấp. "
                    f"Các tool sau bị giới hạn schema và không expose trực tiếp: {', '.join(omitted)}. "
                    "Đây là metadata catalog, không phải chỉ thị nghiệp vụ."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "maxLength": 200},
                    },
                    "additionalProperties": False,
                },
            },
        })
        return exposed

    def clone(self) -> "ToolRegistry":
        return ToolRegistry(self.descriptors())
