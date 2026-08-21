"""Typed errors for LLM provider operations and lifecycle."""

from __future__ import annotations


class LLMError(Exception):
    """Base exception for all LLM errors."""


class LLMNotReadyError(LLMError):
    """Raised when LLM adapter or runtime is not ready."""


class LLMBusyError(LLMError):
    """Raised when LLM concurrency limit is reached and admission times out."""


class LLMAdmissionTimeoutError(LLMBusyError):
    """Raised when admission waiting timed out."""


class LLMTimeoutError(LLMError):
    """Base class for LLM request timeouts."""


class LLMConnectTimeoutError(LLMTimeoutError):
    """Raised when connecting to LLM provider endpoint times out."""


class LLMFirstTokenTimeoutError(LLMTimeoutError):
    """Raised when waiting for the first token stream event times out."""


class LLMTotalTimeoutError(LLMTimeoutError):
    """Raised when total LLM response stream deadline is exceeded."""


class LLMProviderError(LLMError):
    """Base class for HTTP/provider status errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMProviderAuthError(LLMProviderError):
    """Raised on 401/403 HTTP status from provider."""


class LLMProviderRateLimitError(LLMProviderError):
    """Raised on 429 HTTP status from provider."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class LLMProviderUnavailableError(LLMProviderError):
    """Raised on 5xx HTTP status or server connectivity drop."""


class LLMMalformedStreamError(LLMError):
    """Raised when SSE or JSON payload is malformed."""


class LLMOversizedStreamError(LLMError):
    """Raised when response payload exceeds maximum configured bytes."""


class LLMEmptyResponseError(LLMError):
    """Raised when completion finishes with empty content and no tool calls."""


class LLMCircuitOpenError(LLMError):
    """Raised when circuit breaker is OPEN."""
