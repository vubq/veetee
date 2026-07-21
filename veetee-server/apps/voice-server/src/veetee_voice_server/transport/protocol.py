from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

MAX_CONTROL_FRAME_BYTES = 8192
MAX_OPUS_PACKET_BYTES = 1500

SessionId = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=64)]
Reason = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=64)]


class ProtocolViolationError(ValueError):
    def __init__(self, reason: str, *, close_code: int = 1002) -> None:
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DeviceFeatures(StrictModel):
    mcp: bool
    aec: bool
    glyph_push: bool


class AudioParams(StrictModel):
    format: Literal["opus"]
    sample_rate: Literal[16_000, 24_000, 48_000]
    channels: Literal[1]
    frame_duration: Annotated[int, Field(ge=10, le=120)]


class DeviceHello(StrictModel):
    type: Literal["hello"]
    version: Literal[1]
    features: DeviceFeatures
    transport: Literal["websocket"]
    audio_params: AudioParams


class ListenEvent(StrictModel):
    session_id: SessionId
    type: Literal["listen"]
    state: Literal["start", "stop", "detect"]
    mode: Literal["auto", "manual", "realtime"] | None = None
    source: Literal["button", "wake_word"] | None = None
    text: str | None = None
    reason: Reason | None = None


class AbortEvent(StrictModel):
    session_id: SessionId
    type: Literal["abort"]
    reason: Reason = "client_abort"
    source: Literal["button", "wake_word", "interrupt_profile", "server"] | None = None


class SystemEvent(StrictModel):
    session_id: SessionId
    type: Literal["system"]
    command: Literal["assistant_sleep"]
    reason: Reason | None = None


class McpEvent(StrictModel):
    session_id: SessionId
    type: Literal["mcp"]
    payload: dict[str, Any]


ClientEvent = ListenEvent | AbortEvent | SystemEvent | McpEvent


class SttEvent(StrictModel):
    session_id: SessionId
    type: Literal["stt"] = "stt"
    text: str


class TtsEvent(StrictModel):
    session_id: SessionId
    type: Literal["tts"] = "tts"
    state: Literal["start", "sentence_start", "stop"]
    text: str | None = None


class LlmEvent(StrictModel):
    session_id: SessionId
    type: Literal["llm"] = "llm"
    emotion: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    text: str | None = None


def parse_device_hello(
    raw: str,
    *,
    expected_sample_rate: int,
    expected_frame_duration: int,
) -> DeviceHello:
    payload = _json_object(raw)
    try:
        hello = DeviceHello.model_validate(payload)
    except ValidationError as error:
        raise ProtocolViolationError("invalid device hello") from error
    if hello.audio_params.sample_rate != expected_sample_rate:
        raise ProtocolViolationError("unsupported uplink sample rate")
    if hello.audio_params.frame_duration != expected_frame_duration:
        raise ProtocolViolationError("unsupported Opus frame duration")
    return hello


def parse_client_event(raw: str, *, session_id: str) -> ClientEvent:
    payload = _json_object(raw)
    event_type = payload.get("type")
    model: type[ListenEvent] | type[AbortEvent] | type[SystemEvent] | type[McpEvent]
    if event_type == "listen":
        model = ListenEvent
    elif event_type == "abort":
        model = AbortEvent
    elif event_type == "system":
        model = SystemEvent
    elif event_type == "mcp":
        model = McpEvent
    else:
        raise ProtocolViolationError("unsupported client event")
    try:
        event = model.model_validate(payload)
    except ValidationError as error:
        raise ProtocolViolationError("invalid client event") from error
    if event.session_id != session_id:
        raise ProtocolViolationError("session id mismatch", close_code=1008)
    return event


def server_hello_payload(
    session_id: str, *, sample_rate: int, frame_duration: int
) -> dict[str, object]:
    return {
        "type": "hello",
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {
            "format": "opus",
            "sample_rate": sample_rate,
            "channels": 1,
            "frame_duration": frame_duration,
        },
    }


def listen_started_payload(
    session_id: str, *, source: Literal["button", "wake_word"] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session_id,
        "type": "listen",
        "state": "start",
    }
    if source is not None:
        payload["source"] = source
    return ListenEvent.model_validate(payload).model_dump(exclude_none=True)


def stt_payload(session_id: str, text: str) -> dict[str, Any]:
    return SttEvent(session_id=session_id, text=text).model_dump(exclude_none=True)


def tts_payload(
    session_id: str,
    state: Literal["start", "sentence_start", "stop"],
    *,
    text: str | None = None,
) -> dict[str, Any]:
    return TtsEvent(session_id=session_id, state=state, text=text).model_dump(exclude_none=True)


def llm_payload(
    session_id: str, emotion: str, *, text: str | None = None
) -> dict[str, Any]:
    return LlmEvent(session_id=session_id, emotion=emotion, text=text).model_dump(
        exclude_none=True
    )


def mcp_payload(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return McpEvent(session_id=session_id, type="mcp", payload=payload).model_dump()


def assistant_sleep_payload(session_id: str, reason: str) -> dict[str, Any]:
    return SystemEvent(
        session_id=session_id,
        type="system",
        command="assistant_sleep",
        reason=reason,
    ).model_dump(exclude_none=True)


def _json_object(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_CONTROL_FRAME_BYTES:
        raise ProtocolViolationError("control frame too large", close_code=1009)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ProtocolViolationError("malformed JSON control frame") from error
    if not isinstance(payload, dict):
        raise ProtocolViolationError("control frame must be a JSON object")
    return payload
