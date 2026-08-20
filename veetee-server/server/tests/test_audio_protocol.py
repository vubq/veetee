"""Tests for Veetee audio wire protocol v1/v2/v3 parsing, encoding and limits (M1.5)."""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from veetee_server.audio import (
    AudioPacketMetadata,
    MalformedAudioFrameError,
    OversizedAudioFrameError,
    ProtocolVersionMismatchError,
    encode_audio_frame,
    parse_audio_frame,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "device"

# Shared golden payload: raw Opus-like bytes reused across versions.
V1_PAYLOAD = bytes.fromhex("f8fffe0102030405")

V2_VALID_HEX = "0002000000000000000003e800000008f8fffe0102030405"
V3_VALID_HEX = "00000008f8fffe0102030405"


def _load_golden(name: str) -> dict[str, Any]:
    path = CONTRACTS_DIR / name
    assert path.exists(), f"Missing golden file: {path}"
    with path.open(encoding="utf-8") as fh:
        return cast(dict[str, Any], json.load(fh))


def test_v1_roundtrip() -> None:
    packet = parse_audio_frame(V1_PAYLOAD, negotiated_version=1)
    assert packet == AudioPacketMetadata(
        protocol_version=1,
        type=0,
        timestamp_ms=None,
        payload_size=len(V1_PAYLOAD),
        payload=V1_PAYLOAD,
    )
    assert encode_audio_frame(packet) == V1_PAYLOAD


def test_v1_max_payload_boundary() -> None:
    # Exactly at the configured max is accepted.
    packet = parse_audio_frame(b"\x00" * 8, negotiated_version=1, max_payload_bytes=8)
    assert packet.payload_size == 8
    # One byte beyond the max is rejected.
    with pytest.raises(OversizedAudioFrameError):
        parse_audio_frame(b"\x00" * 9, negotiated_version=1, max_payload_bytes=8)


def test_v2_roundtrip() -> None:
    raw = bytes.fromhex(V2_VALID_HEX)
    packet = parse_audio_frame(raw, negotiated_version=2)
    assert packet == AudioPacketMetadata(
        protocol_version=2,
        type=0,
        timestamp_ms=1000,
        payload_size=8,
        payload=V1_PAYLOAD,
    )
    assert encode_audio_frame(packet) == raw


def test_v3_roundtrip() -> None:
    raw = bytes.fromhex(V3_VALID_HEX)
    packet = parse_audio_frame(raw, negotiated_version=3)
    assert packet == AudioPacketMetadata(
        protocol_version=3,
        type=0,
        timestamp_ms=None,
        payload_size=8,
        payload=V1_PAYLOAD,
    )
    assert encode_audio_frame(packet) == raw


def test_unsupported_negotiated_version() -> None:
    with pytest.raises(ProtocolVersionMismatchError):
        parse_audio_frame(V1_PAYLOAD, negotiated_version=0)
    with pytest.raises(ProtocolVersionMismatchError):
        parse_audio_frame(V1_PAYLOAD, negotiated_version=4)


def test_v2_encode_requires_timestamp() -> None:
    packet = AudioPacketMetadata(
        protocol_version=2,
        type=0,
        timestamp_ms=None,
        payload_size=len(V1_PAYLOAD),
        payload=V1_PAYLOAD,
    )
    with pytest.raises(MalformedAudioFrameError):
        encode_audio_frame(packet)


@pytest.mark.parametrize(
    "packet",
    [
        AudioPacketMetadata(1, 0, None, 2, b"x"),
        AudioPacketMetadata(1, 1, None, 1, b"x"),
        AudioPacketMetadata(2, 0, -1, 1, b"x"),
        AudioPacketMetadata(2, 0, 0x1_0000_0000, 1, b"x"),
        AudioPacketMetadata(3, 0, None, 0, b""),
    ],
)
def test_encode_rejects_invalid_metadata(packet: AudioPacketMetadata) -> None:
    with pytest.raises(MalformedAudioFrameError):
        encode_audio_frame(packet)


def test_encode_unsupported_version() -> None:
    # protocol_version is typed Literal[1,2,3]; cast to simulate a bad runtime value.
    bad = cast(Any, AudioPacketMetadata)(
        protocol_version=9,
        type=0,
        timestamp_ms=None,
        payload_size=len(V1_PAYLOAD),
        payload=V1_PAYLOAD,
    )
    with pytest.raises(ProtocolVersionMismatchError):
        encode_audio_frame(bad)


def test_golden_valid_vectors() -> None:
    """Every valid golden vector must parse and re-encode byte-identically."""
    for name in ("audio_v1_golden.json", "audio_v2_golden.json", "audio_v3_golden.json"):
        doc = _load_golden(name)
        version = doc["version"]
        for vector in doc["vectors"]:
            raw = bytes.fromhex(vector["hex_payload"])
            packet = parse_audio_frame(raw, negotiated_version=version)
            assert packet.protocol_version == vector["expected"]["protocol_version"]
            assert packet.type == vector["expected"]["type"]
            assert packet.timestamp_ms == vector["expected"]["timestamp_ms"]
            assert packet.payload_size == vector["expected"]["payload_size"]
            assert encode_audio_frame(packet) == raw


def test_golden_malformed_vectors() -> None:
    """Every malformed golden vector must raise the declared exception."""
    doc = _load_golden("audio_malformed_golden.json")
    error_classes = {
        "MalformedAudioFrameError": MalformedAudioFrameError,
        "OversizedAudioFrameError": OversizedAudioFrameError,
        "ProtocolVersionMismatchError": ProtocolVersionMismatchError,
    }
    assert doc["vectors"], "malformed golden file must not be empty"
    for vector in doc["vectors"]:
        raw = bytes.fromhex(vector["hex_payload"])
        max_payload_bytes = vector.get("max_payload_bytes", 65536)
        expected = error_classes[vector["expected_error"]]
        with pytest.raises(expected):
            parse_audio_frame(
                raw,
                negotiated_version=vector["version"],
                max_payload_bytes=max_payload_bytes,
            )


def test_golden_files_are_valid_json() -> None:
    for name in (
        "audio_v1_golden.json",
        "audio_v2_golden.json",
        "audio_v3_golden.json",
        "audio_malformed_golden.json",
    ):
        doc = _load_golden(name)
        assert isinstance(doc, dict)
        assert "vectors" in doc
