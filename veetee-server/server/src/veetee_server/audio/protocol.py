"""Wire frame parsing, encoding, and metadata validation for Veetee Audio frames."""

import struct
from dataclasses import dataclass
from typing import Literal


class AudioError(Exception):
    """Base exception for audio domain errors."""


class AudioProtocolError(AudioError):
    """Base exception for audio protocol wire framing errors."""


class MalformedAudioFrameError(AudioProtocolError):
    """Raised when an audio frame structure or header is malformed."""


class OversizedAudioFrameError(AudioProtocolError):
    """Raised when an audio frame exceeds maximum allowed payload or frame size."""


class ProtocolVersionMismatchError(AudioProtocolError):
    """Raised when a frame header version does not match negotiated version."""


@dataclass(frozen=True, slots=True)
class AudioPacketMetadata:
    """Typed immutable audio packet metadata."""

    protocol_version: Literal[1, 2, 3]
    type: int
    timestamp_ms: int | None
    payload_size: int
    payload: bytes


V2_HEADER_LEN = 16
V3_HEADER_LEN = 4


def parse_audio_frame(
    data: bytes,
    negotiated_version: int,
    max_payload_bytes: int = 65536,
) -> AudioPacketMetadata:
    """Parses and strictly validates binary audio frame according to protocol version.

    Validation rules:
    - Rejects empty data.
    - Validates header size and fields before allocating or slicing payload.
    - Ensures declared payload size matches actual remaining bytes.
    - Enforces protocol version, message type (0=OPUS), and reserved fields policy.
    - No fake timestamp generated for Version 1 or Version 3 packets.
    """
    if not data:
        raise MalformedAudioFrameError("Received empty audio frame")

    if negotiated_version not in (1, 2, 3):
        raise ProtocolVersionMismatchError(f"Unsupported protocol version: {negotiated_version}")

    if negotiated_version == 1:
        if len(data) > max_payload_bytes:
            raise OversizedAudioFrameError(
                f"Audio payload size {len(data)} exceeds maximum allowed limit {max_payload_bytes}"
            )
        return AudioPacketMetadata(
            protocol_version=1,
            type=0,
            timestamp_ms=None,
            payload_size=len(data),
            payload=data,
        )

    if negotiated_version == 2:
        if len(data) < V2_HEADER_LEN:
            raise MalformedAudioFrameError(
                f"Truncated V2 header: expected {V2_HEADER_LEN} bytes, got {len(data)}"
            )
        version, msg_type, reserved, timestamp, payload_size = struct.unpack(
            ">HHIII", data[:V2_HEADER_LEN]
        )
        if version != 2:
            raise ProtocolVersionMismatchError(
                f"V2 header version mismatch: expected 2, got {version}"
            )
        if msg_type != 0:
            raise MalformedAudioFrameError(f"Invalid V2 message type: expected 0, got {msg_type}")
        if reserved != 0:
            raise MalformedAudioFrameError(
                f"Non-zero reserved field in V2 header: expected 0, got {reserved}"
            )
        if payload_size > max_payload_bytes:
            raise OversizedAudioFrameError(
                f"Declared V2 payload size {payload_size} exceeds limit {max_payload_bytes}"
            )
        if len(data) != V2_HEADER_LEN + payload_size:
            actual_payload = len(data) - V2_HEADER_LEN
            raise MalformedAudioFrameError(
                f"V2 payload size mismatch: header declared {payload_size}, "
                f"actual payload is {actual_payload}"
            )
        return AudioPacketMetadata(
            protocol_version=2,
            type=0,
            timestamp_ms=timestamp,
            payload_size=payload_size,
            payload=data[V2_HEADER_LEN:],
        )

    # negotiated_version == 3
    if len(data) < V3_HEADER_LEN:
        raise MalformedAudioFrameError(
            f"Truncated V3 header: expected {V3_HEADER_LEN} bytes, got {len(data)}"
        )
    msg_type, reserved, payload_size = struct.unpack(">BBH", data[:V3_HEADER_LEN])
    if msg_type != 0:
        raise MalformedAudioFrameError(f"Invalid V3 message type: expected 0, got {msg_type}")
    if reserved != 0:
        raise MalformedAudioFrameError(
            f"Non-zero reserved field in V3 header: expected 0, got {reserved}"
        )
    if payload_size > max_payload_bytes:
        raise OversizedAudioFrameError(
            f"Declared V3 payload size {payload_size} exceeds limit {max_payload_bytes}"
        )
    if len(data) != V3_HEADER_LEN + payload_size:
        actual_payload = len(data) - V3_HEADER_LEN
        raise MalformedAudioFrameError(
            f"V3 payload size mismatch: header declared {payload_size}, "
            f"actual payload is {actual_payload}"
        )
    return AudioPacketMetadata(
        protocol_version=3,
        type=0,
        timestamp_ms=None,
        payload_size=payload_size,
        payload=data[V3_HEADER_LEN:],
    )


def encode_audio_frame(packet: AudioPacketMetadata) -> bytes:
    """Encodes an AudioPacketMetadata back into raw network byte order wire bytes."""
    if not packet.payload or packet.payload_size != len(packet.payload):
        raise MalformedAudioFrameError("Audio packet payload metadata is inconsistent")
    if packet.type != 0:
        raise MalformedAudioFrameError("Only Opus audio packet type 0 is supported")
    if packet.protocol_version == 1:
        return packet.payload
    if packet.protocol_version == 2:
        if packet.timestamp_ms is None or not 0 <= packet.timestamp_ms <= 0xFFFFFFFF:
            raise MalformedAudioFrameError("V2 timestamp must be an unsigned 32-bit value")
        ts = packet.timestamp_ms
        header = struct.pack(">HHIII", 2, packet.type, 0, ts, len(packet.payload))
        return header + packet.payload
    if packet.protocol_version == 3:
        if len(packet.payload) > 0xFFFF:
            raise OversizedAudioFrameError("V3 payload exceeds unsigned 16-bit size")
        header = struct.pack(">BBH", packet.type, 0, len(packet.payload))
        return header + packet.payload
    raise ProtocolVersionMismatchError(
        f"Unsupported packet protocol version: {packet.protocol_version}"
    )
