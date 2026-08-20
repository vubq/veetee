"""Opus codec primitives, strict PCM format contracts, and deferred native boundaries."""

from dataclasses import dataclass
from typing import Literal, Protocol

from .protocol import AudioError


class CodecError(AudioError):
    """Base exception for audio codec errors."""


class DeferredNativeCodecError(CodecError):
    """Raised when native Opus codec execution is attempted before native dependencies."""


class InvalidPCMFormatError(CodecError):
    """Raised when PCM buffer does not match specified sample rate, frame or channel rules."""


class DeferredNativeResamplerError(AudioError):
    """Raised when native high-quality resampling is requested before native dependencies."""


@dataclass(frozen=True, slots=True)
class PCMFormat:
    """Strict contract specification for raw PCM audio buffers."""

    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_duration_ms: int
    endianness: Literal["little"] = "little"

    @property
    def expected_samples(self) -> int:
        return (self.sample_rate * self.frame_duration_ms) // 1000

    @property
    def expected_bytes(self) -> int:
        return self.expected_samples * self.channels * self.sample_width_bytes

    def validate_buffer(self, pcm_data: bytes) -> None:
        """Validates a PCM buffer strictly matches expected byte size and format constraints."""
        if len(pcm_data) != self.expected_bytes:
            raise InvalidPCMFormatError(
                f"PCM buffer length mismatch: expected {self.expected_bytes} bytes "
                f"({self.expected_samples} samples at {self.sample_rate}Hz "
                f"{self.frame_duration_ms}ms), got {len(pcm_data)} bytes"
            )


# Standard Veetee PCM Formats
UPLINK_PCM_FORMAT = PCMFormat(
    sample_rate=16000,
    channels=1,
    sample_width_bytes=2,
    frame_duration_ms=60,
    endianness="little",
)

DOWNLINK_PCM_FORMAT = PCMFormat(
    sample_rate=24000,
    channels=1,
    sample_width_bytes=2,
    frame_duration_ms=60,
    endianness="little",
)


class AudioDecoder(Protocol):
    """Protocol boundary for Opus decoders."""

    def decode(self, payload: bytes) -> bytes: ...


class AudioEncoder(Protocol):
    """Protocol boundary for Opus encoders."""

    def encode(self, pcm_data: bytes) -> bytes: ...


class AudioResampler(Protocol):
    """Protocol boundary for PCM resamplers."""

    def resample(
        self, pcm_data: bytes, source_format: PCMFormat, target_format: PCMFormat
    ) -> bytes: ...


class DeferredNativeAudioDecoder:
    """Production Opus decoder boundary; defers until native libopus is integrated."""

    def decode(self, payload: bytes) -> bytes:
        raise DeferredNativeCodecError(
            "Native Opus decoder implementation is deferred until native libopus "
            "dependencies are integrated"
        )


class DeferredNativeAudioEncoder:
    """Production Opus encoder boundary; defers until native libopus is integrated."""

    def encode(self, pcm_data: bytes) -> bytes:
        raise DeferredNativeCodecError(
            "Native Opus encoder implementation is deferred until native libopus "
            "dependencies are integrated"
        )


class DeferredNativeAudioResampler:
    """Strict resampler boundary refusing fake low-quality resampling implementations."""

    def resample(
        self, pcm_data: bytes, source_format: PCMFormat, target_format: PCMFormat
    ) -> bytes:
        source_format.validate_buffer(pcm_data)
        if source_format == target_format:
            return pcm_data
        raise DeferredNativeResamplerError(
            f"Native resampling from {source_format.sample_rate}Hz to "
            f"{target_format.sample_rate}Hz is deferred; "
            f"fake linear/nearest interpolation is strictly prohibited."
        )


class FakeOpusDecoder:
    """Deterministic fake Opus decoder for testing and pipeline harness without native libopus."""

    def __init__(self, pcm_format: PCMFormat = UPLINK_PCM_FORMAT) -> None:
        self.pcm_format = pcm_format

    def decode(self, payload: bytes) -> bytes:
        if not payload:
            raise CodecError("Cannot decode empty Opus payload")
        # Generates deterministic PCM payload of exact expected length
        pattern = payload * (self.pcm_format.expected_bytes // len(payload) + 1)
        return pattern[: self.pcm_format.expected_bytes]


class FakeOpusEncoder:
    """Deterministic fake Opus encoder for testing and pipeline harness without native libopus."""

    def __init__(self, pcm_format: PCMFormat = DOWNLINK_PCM_FORMAT) -> None:
        self.pcm_format = pcm_format

    def encode(self, pcm_data: bytes) -> bytes:
        self.pcm_format.validate_buffer(pcm_data)
        # Encodes with a deterministic header tag + sample prefix
        return b"\xf8\xff\xfe" + pcm_data[:8]
