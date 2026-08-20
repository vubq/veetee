"""Wire frame building for pipeline-generated downlink audio (M1.6).

The fake TTS produces PCM; the encoder produces an Opus payload; this module
wraps that payload into the device's negotiated wire format. Version 1 is the
raw Opus payload, version 2 adds the 16-byte header with a millisecond
timestamp, and version 3 adds the compact 4-byte header (no timestamp).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from veetee_server.audio.protocol import (
    AudioError,
    AudioPacketMetadata,
    encode_audio_frame,
)


def build_downlink_frame(
    protocol_version: int,
    opus_payload: bytes,
    timestamp_ms: int | None = None,
    now_ms: Callable[[], int] | None = None,
) -> bytes:
    """Encodes an Opus payload into the negotiated wire format.

    ``timestamp_ms`` is only used for version 2 frames; when omitted it is
    generated via ``now_ms`` (defaults to wall-clock milliseconds), keeping
    the helper deterministic for tests.
    """
    if not opus_payload:
        raise AudioError("Cannot build a downlink frame from an empty Opus payload")

    if protocol_version == 1:
        return opus_payload
    if protocol_version == 2:
        ts = timestamp_ms
        if ts is None:
            ts = (now_ms or (lambda: int(time.time() * 1000)))() & 0xFFFFFFFF
        return encode_audio_frame(
            AudioPacketMetadata(
                protocol_version=2,
                type=0,
                timestamp_ms=ts,
                payload_size=len(opus_payload),
                payload=opus_payload,
            )
        )
    if protocol_version == 3:
        return encode_audio_frame(
            AudioPacketMetadata(
                protocol_version=3,
                type=0,
                timestamp_ms=None,
                payload_size=len(opus_payload),
                payload=opus_payload,
            )
        )
    raise AudioError(f"Unsupported protocol version for downlink framing: {protocol_version}")
