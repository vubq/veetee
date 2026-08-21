"""Typed contracts, dataclasses, and error taxonomy for VAD providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from .fake import SpeechSegment, VadEvent, VadEventKind

__all__ = [
    "BaseVADStream",
    "SileroVADConfig",
    "SpeechSegment",
    "VADAdmissionTimeoutError",
    "VADEndReason",
    "VADEngineProtocol",
    "VADError",
    "VADModelError",
    "VADNotReadyError",
    "VADSampleOffset",
    "VADUtterance",
    "VadEvent",
    "VadEventKind",
]


class VADEndReason(StrEnum):
    """Reason why a speech utterance concluded."""

    END_SILENCE = "end_silence"
    MAX_UTTERANCE = "max_utterance"
    STREAM_END = "stream_end"


@dataclass(frozen=True, slots=True)
class VADSampleOffset:
    """Sample and time offsets relative to stream start."""

    sample_offset: int
    ms_offset: float


@dataclass(frozen=True, slots=True)
class VADUtterance:
    """Finalized speech utterance with typed offsets, PCM metadata, and raw PCM bytes."""

    start_sample: int
    end_sample: int
    start_ms: float
    end_ms: float
    pcm_data: bytes
    end_reason: VADEndReason = VADEndReason.END_SILENCE
    sample_rate: int = 16000
    sample_width: int = 2
    channels: int = 1


@dataclass(frozen=True, slots=True)
class SileroVADConfig:
    """Typed configuration parameters for Silero VAD processing."""

    sample_rate: int = 16000
    sample_width: int = 2
    channels: int = 1
    window_samples: int = 512  # 32 ms window at 16 kHz
    threshold: float = 0.5
    neg_threshold: float = 0.35
    pre_roll_ms: int = 80
    min_speech_ms: int = 250
    end_silence_ms: int = 150
    max_utterance_ms: int = 12000
    max_concurrency: int = 4
    admission_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.sample_rate != 16000:
            raise ValueError("Silero VAD requires a 16000 Hz sample rate")
        if self.sample_width != 2:
            raise ValueError("Silero VAD requires 16-bit PCM")
        if self.channels != 1:
            raise ValueError("Silero VAD requires mono PCM")
        if self.window_samples != 512:
            raise ValueError("Silero VAD requires 512-sample windows at 16 kHz")
        if not (0.0 <= self.threshold <= 1.0):
            raise ValueError("threshold must be between 0.0 and 1.0")
        if not (0.0 <= self.neg_threshold <= 1.0):
            raise ValueError("neg_threshold must be between 0.0 and 1.0")
        if self.neg_threshold > self.threshold:
            raise ValueError("neg_threshold must not exceed threshold")
        if self.pre_roll_ms < 0:
            raise ValueError("pre_roll_ms must be non-negative")
        if self.min_speech_ms < 0:
            raise ValueError("min_speech_ms must be non-negative")
        if self.end_silence_ms < 0:
            raise ValueError("end_silence_ms must be non-negative")
        if self.max_utterance_ms < self.min_speech_ms:
            raise ValueError("max_utterance_ms must be at least min_speech_ms")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if self.admission_timeout_seconds <= 0:
            raise ValueError("admission_timeout_seconds must be positive")

    @property
    def window_bytes(self) -> int:
        return self.window_samples * self.sample_width * self.channels

    @property
    def pre_roll_samples(self) -> int:
        return int(self.pre_roll_ms * self.sample_rate / 1000)

    @property
    def min_speech_samples(self) -> int:
        return int(self.min_speech_ms * self.sample_rate / 1000)

    @property
    def end_silence_samples(self) -> int:
        return int(self.end_silence_ms * self.sample_rate / 1000)

    @property
    def max_utterance_samples(self) -> int:
        return int(self.max_utterance_ms * self.sample_rate / 1000)


class VADError(Exception):
    """Base exception for VAD provider failures."""


class VADNotReadyError(VADError):
    """Raised when VAD runtime or model is unready."""


class VADAdmissionTimeoutError(VADError):
    """Raised when VAD concurrency limit is reached and admission times out."""


class VADModelError(VADError):
    """Raised when VAD model file or runtime loading fails."""


@runtime_checkable
class VADEngineProtocol(Protocol):
    """Protocol for underlying inference engine (ONNX runtime or deterministic mock)."""

    @property
    def is_ready(self) -> bool:
        ...

    def initial_state(self) -> Any:
        ...

    def run_inference(self, pcm_512: bytes, state: Any) -> tuple[float, Any]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class BaseVADStream(Protocol):
    """Per-turn VAD stream protocol."""

    async def process_pcm_async(self, pcm_bytes: bytes) -> list[VadEvent]:
        ...

    def finish(self) -> VADUtterance | SpeechSegment | None:
        ...

    def reset(self) -> None:
        ...
