"""Typed contracts, dataclasses, and error taxonomy for ASR providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ASRAdmissionTimeoutError",
    "ASREngineProtocol",
    "ASRError",
    "ASRModelError",
    "ASRNotReadyError",
    "ASROversizedAudioError",
    "ASRProvider",
    "ASRResult",
    "ASRSegment",
    "ASRTimeoutError",
    "ASRTranscribeRequest",
    "ASRValidationError",
    "PhoWhisperConfig",
    "normalize_transcript",
]


@dataclass(frozen=True, slots=True)
class ASRSegment:
    """Typed segment metadata returned by ASR providers."""

    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: list[int] = field(default_factory=list)
    temperature: float = 0.0
    avg_logprob: float = 0.0
    compression_ratio: float = 1.0
    no_speech_prob: float = 0.0


@dataclass(frozen=True, slots=True)
class ASRResult:
    """Typed result from transcription."""

    raw_text: str
    normalized_text: str
    language: str = "vi"
    duration_seconds: float = 0.0
    segments: list[ASRSegment] = field(default_factory=list)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ASRTranscribeRequest:
    """Typed request container for PCM transcription."""

    pcm_data: bytes
    sample_rate: int = 16000
    sample_width: int = 2
    channels: int = 1
    language: str | None = None


@dataclass(frozen=True, slots=True)
class PhoWhisperConfig:
    """Typed configuration for PhoWhisper runtime."""

    model_id: str = "mad1999/pho-whisper-small-ct2"
    device: str = "cuda"
    compute_type: str = "float16"
    max_concurrency: int = 1
    admission_timeout_seconds: float = 2.0
    total_timeout_seconds: float = 10.0
    max_audio_seconds: float = 30.0
    language: str = "vi"
    download_root: str | None = None
    local_files_only: bool = True

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if self.admission_timeout_seconds <= 0:
            raise ValueError("admission_timeout_seconds must be positive")
        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        if self.max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")


def normalize_transcript(raw_text: str) -> str:
    """Controlled normalization for ASR transcript output.

    Strips leading/trailing whitespace and collapses internal multiple spaces.
    Does not alter words or punctuation unless required.
    """
    if not raw_text:
        return ""
    lines = raw_text.strip().splitlines()
    cleaned_lines = [" ".join(line.split()) for line in lines if line.strip()]
    return " ".join(cleaned_lines)


class ASRError(Exception):
    """Base exception for ASR provider failures."""


class ASRNotReadyError(ASRError):
    """Raised when ASR runtime or model is unready."""


class ASRAdmissionTimeoutError(ASRError):
    """Raised when ASR concurrency limit is reached and admission times out."""


class ASRTimeoutError(ASRError):
    """Raised when total transcription timeout is exceeded."""


class ASRModelError(ASRError):
    """Raised when ASR model file or runtime loading fails."""


class ASRValidationError(ASRError):
    """Raised when PCM data validation fails."""


class ASROversizedAudioError(ASRValidationError):
    """Raised when audio duration exceeds configured max_audio_seconds."""


@runtime_checkable
class ASREngineProtocol(Protocol):
    """Protocol for underlying inference engine (faster-whisper/CTranslate2 or test mock)."""

    @property
    def is_ready(self) -> bool:
        ...

    def run_inference(
        self, pcm_float32: Any, language: str
    ) -> tuple[str, list[ASRSegment], dict[str, Any]]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class ASRProvider(Protocol):
    """Async ASR boundary consumed by the realtime pipeline."""

    async def transcribe_async(self, request: Any) -> ASRResult:
        ...
