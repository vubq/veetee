"""Protocol schemas and validation utilities for Veetee Device WebSocket."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# M6.7 device MCP envelope bounds. The outer frame is already bounded by the
# global JSON size/depth checks; these limits bound the JSON-RPC fields so a
# hostile device cannot smuggle oversized identifiers into correlation state.
MCP_METHOD_MAX_LENGTH = 128
MCP_CORRELATION_ID_MAX_LENGTH = 128
MCP_ERROR_MESSAGE_MAX_LENGTH = 512


def make_error_envelope(code: str, message: str, session_id: str | None = None) -> dict[str, Any]:
    """Generates a safe error envelope matching M0 taxonomy."""
    return {
        "code": code,
        "message": message,
        "session_id": session_id,
    }


def _reject_nonstandard_constant(value: str) -> Any:
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_and_validate_json(raw: str | bytes, max_depth: int = 8) -> dict[str, Any]:
    """Parses JSON text and strictly validates depth and object root structure."""
    parsed = json.loads(
        raw,
        parse_constant=_reject_nonstandard_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("Root JSON payload must be an object")

    def _check_depth(obj: Any, current_depth: int = 1) -> None:
        if current_depth > max_depth:
            raise ValueError(f"JSON depth limit {max_depth} exceeded")
        if isinstance(obj, dict):
            for v in obj.values():
                _check_depth(v, current_depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                _check_depth(item, current_depth + 1)

    _check_depth(parsed)
    return parsed


class UplinkAudioParams(BaseModel):
    """Audio profile expected from device during uplink hello."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["opus"]
    sample_rate: Literal[16000]
    channels: Literal[1]
    frame_duration: Literal[60]


class TextFontCapability(BaseModel):
    """Optional bounded glyph bundle metadata advertised by display firmware."""

    model_config = ConfigDict(extra="forbid")

    bundle: str = Field(min_length=1, max_length=128)
    charset: str = Field(min_length=1, max_length=128)
    size: int = Field(gt=0, le=256)
    bpp: Literal[1, 2, 4, 8]


class HelloMessage(BaseModel):
    """Device hello message specification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"]
    version: Literal[1]
    transport: Literal["websocket"]
    features: dict[str, bool] | None = Field(default=None)
    text_font: TextFontCapability | None = None
    audio_params: UplinkAudioParams

    @field_validator("features")
    @classmethod
    def validate_features(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v is not None:
            if len(v) > 16:
                raise ValueError("Features mapping exceeds maximum allowed size of 16 keys")
            for k in v.keys():
                if len(k) > 64:
                    raise ValueError("Feature name exceeds maximum length of 64 characters")
        return v


class ListenMessage(BaseModel):
    """Control frame for speech activity state."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["listen"]
    state: Literal["start", "stop", "detect"]
    mode: Literal["auto", "manual", "realtime"] | None = Field(default=None)
    session_id: str | None = Field(default=None, max_length=128)
    text: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_state_fields(self) -> "ListenMessage":
        if self.text is not None and self.state != "detect":
            raise ValueError("listen text is only valid for detect state")
        return self


class SessionMessage(BaseModel):
    """Strict schema for control messages with no message-specific payload."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["ping", "pong", "abort", "goodbye"]
    session_id: str | None = Field(default=None, max_length=128)


class AbortMessage(BaseModel):
    """Abort control frame, including the firmware wake-word reason."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["abort"]
    session_id: str | None = Field(default=None, max_length=128)
    reason: Literal["wake_word_detected"] | None = None


class McpMessage(BaseModel):
    """Strict outer envelope for device MCP frames (Quyết định Veetee - M6.7).

    The JSON-RPC 2.0 payload is carried inside ``payload`` and validated
    separately by :func:`parse_device_mcp_response` so responses can be routed
    to a pending correlation entry while requests/notifications are rejected
    or ignored safely without tearing down the session.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp"]
    session_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]


def parse_device_mcp_response(payload: dict[str, Any]) -> str | int:
    """Validates a strict JSON-RPC 2.0 response object and returns its id.

    Raises :class:`ValueError` for any structural violation. A valid response
    must declare ``jsonrpc == "2.0"``, carry exactly one of ``result`` or
    ``error`` and use a bounded correlation id. Requests (``method`` present)
    are not valid responses and are reported as such.
    """
    if not isinstance(payload, dict):
        raise ValueError("MCP payload must be an object")
    if "method" in payload:
        raise ValueError("MCP response must not carry a method field")
    allowed_keys = {"jsonrpc", "id", "result", "error"}
    unknown = set(payload) - allowed_keys
    if unknown:
        raise ValueError(f"Unknown MCP payload fields: {sorted(unknown)}")
    if payload.get("jsonrpc") != "2.0":
        raise ValueError("MCP payload must declare jsonrpc '2.0'")

    rpc_id = payload.get("id")
    if isinstance(rpc_id, bool) or not isinstance(rpc_id, (str, int)):
        raise ValueError("MCP response id must be a string or integer")
    if isinstance(rpc_id, str):
        if not rpc_id or len(rpc_id) > MCP_CORRELATION_ID_MAX_LENGTH:
            raise ValueError("MCP response id length out of bounds")
    elif rpc_id < 0 or rpc_id > 9223372036854775807:
        raise ValueError("MCP response integer id must be non-negative")

    has_result = "result" in payload
    has_error = "error" in payload
    if has_result == has_error:
        raise ValueError("MCP response must carry exactly one of result or error")

    if has_error:
        error = payload["error"]
        if not isinstance(error, dict):
            raise ValueError("MCP response error must be an object")
        unknown_error_keys = set(error) - {"code", "message", "data"}
        if unknown_error_keys:
            raise ValueError(f"Unknown MCP error fields: {sorted(unknown_error_keys)}")
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("MCP error code must be an integer")
        if not isinstance(message, str) or not message:
            raise ValueError("MCP error message must be a non-empty string")
        if len(message) > MCP_ERROR_MESSAGE_MAX_LENGTH:
            raise ValueError("MCP error message too long")
    return rpc_id


def build_device_mcp_request(
    *, correlation_id: str, method: str, params: dict[str, Any], session_id: str
) -> dict[str, Any]:
    """Builds the server -> device MCP envelope with a JSON-RPC request body."""
    return {
        "type": "mcp",
        "session_id": session_id,
        "payload": {
            "jsonrpc": "2.0",
            "id": correlation_id,
            "method": method,
            "params": params,
        },
    }
