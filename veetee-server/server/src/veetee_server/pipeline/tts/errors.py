"""Typed errors raised by Gemini TTS adapter and key pool."""

from __future__ import annotations


class TTSError(Exception):
    """Base exception for all TTS errors."""


class TTSNotReadyError(TTSError):
    """Raised when Gemini TTS runtime is not ready."""


class TTSAdmissionTimeoutError(TTSError):
    """Raised when request times out waiting for global concurrency admission."""


class TTSConnectTimeoutError(TTSError):
    """Raised when connection to Gemini API times out."""


class TTSFirstAudioTimeoutError(TTSError):
    """Raised when first PCM audio chunk is not received within deadline."""


class TTSTotalTimeoutError(TTSError):
    """Raised when total TTS synthesis duration exceeds total timeout."""


class TTSFormatError(TTSError):
    """Raised when PCM buffer format or mimeType is invalid."""


class TTSKeyExhaustedError(TTSError):
    """Raised when all API keys in the key pool are disabled or in cooldown."""


class TTSProviderError(TTSError):
    """Base exception for Gemini provider errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TTSProviderAuthError(TTSProviderError):
    """Raised on 401/403 HTTP status from provider."""


class TTSProviderRateLimitError(TTSProviderError):
    """Raised on 429 HTTP status from provider."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class TTSProviderUnavailableError(TTSProviderError):
    """Raised on 5xx or connection disconnects from provider."""


class TTSMalformedStreamError(TTSProviderError):
    """Raised on malformed SSE events or base64 decoding errors."""
