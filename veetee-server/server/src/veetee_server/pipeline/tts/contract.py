"""Typed configuration contracts and provider events for Gemini TTS (M2.5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GeminiTTSConfig:
    """Typed runtime settings for Gemini native TTS streaming."""

    api_keys: list[str] = field(default_factory=list)
    main_model: str = "gemini-3.1-flash-tts-preview"
    fallback_model: str = "gemini-2.5-flash-preview-tts"
    enable_fallback_model: bool = False
    voice: str = "Kore"
    prompt_prefix: str = ""
    connect_timeout_seconds: float = 3.0
    first_audio_timeout_seconds: float = 5.0
    total_timeout_seconds: float = 30.0
    max_concurrency: int = 4
    admission_timeout_seconds: float = 2.0
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: float = 10.0
    max_retry_after_seconds: float = 60.0
    max_response_bytes: int = 8_388_608

    def __post_init__(self) -> None:
        if not self.api_keys:
            raise ValueError("api_keys must not be empty")
        if any(not key.strip() for key in self.api_keys):
            raise ValueError("api_keys must not contain empty values")
        positive = (
            self.connect_timeout_seconds,
            self.first_audio_timeout_seconds,
            self.total_timeout_seconds,
            self.admission_timeout_seconds,
            self.circuit_breaker_cooldown_seconds,
            self.max_retry_after_seconds,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("TTS timeouts and cooldowns must be positive")
        if self.total_timeout_seconds <= max(
            self.connect_timeout_seconds, self.first_audio_timeout_seconds
        ):
            raise ValueError("total_timeout_seconds must cover connect and first-audio timeout")
        if self.max_concurrency <= 0 or self.circuit_breaker_failure_threshold <= 0:
            raise ValueError("TTS concurrency and failure threshold must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


@dataclass(frozen=True, slots=True)
class TTSStartedEvent:
    """Emitted when Gemini TTS synthesis request starts."""

    model: str
    voice: str
    request_id: str


@dataclass(frozen=True, slots=True)
class TTSAudioChunkEvent:
    """Emitted when a resampled PCM audio chunk is received."""

    pcm: bytes
    duration_ms: float
    sample_rate: int = 24000


@dataclass(frozen=True, slots=True)
class TTSCompletedEvent:
    """Emitted when TTS synthesis for a segment successfully finishes."""

    request_id: str
    total_bytes: int
    total_duration_ms: float


@dataclass(frozen=True, slots=True)
class TTSFailedEvent:
    """Emitted when TTS synthesis fails."""

    request_id: str
    error_code: str
    message: str
