"""Protocol schemas and validation utilities for Veetee Device WebSocket."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class HelloMessage(BaseModel):
    """Device hello message specification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"]
    version: Literal[1]
    transport: Literal["websocket"]
    features: dict[str, bool] | None = Field(default=None)
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


class SessionMessage(BaseModel):
    """Strict schema for control messages with no message-specific payload."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["ping", "pong", "abort", "goodbye"]
    session_id: str | None = Field(default=None, max_length=128)
