"""Text-to-Speech pipeline adapters, key pool, and contracts for Veetee Server (M2.5)."""

from .contract import (
    GeminiTTSConfig,
    TTSAudioChunkEvent,
    TTSCompletedEvent,
    TTSFailedEvent,
    TTSStartedEvent,
)
from .errors import (
    TTSAdmissionTimeoutError,
    TTSConnectTimeoutError,
    TTSError,
    TTSFirstAudioTimeoutError,
    TTSFormatError,
    TTSKeyExhaustedError,
    TTSMalformedStreamError,
    TTSNotReadyError,
    TTSProviderAuthError,
    TTSProviderError,
    TTSProviderRateLimitError,
    TTSProviderUnavailableError,
    TTSTotalTimeoutError,
)
from .fake import FakeTTS
from .gemini import GeminiTTSAdapter, GeminiTTSRuntime, extract_pcm_from_gemini_response
from .key_pool import GeminiKeyPool, KeyEntry, KeyPoolState
from .vieneu import VieNeuTTSAdapter, VieNeuTTSRuntime

__all__ = [
    "FakeTTS",
    "GeminiKeyPool",
    "GeminiTTSAdapter",
    "GeminiTTSConfig",
    "GeminiTTSRuntime",
    "VieNeuTTSAdapter",
    "VieNeuTTSRuntime",
    "KeyEntry",
    "KeyPoolState",
    "TTSAdmissionTimeoutError",
    "TTSAudioChunkEvent",
    "TTSCompletedEvent",
    "TTSConnectTimeoutError",
    "TTSError",
    "TTSFailedEvent",
    "TTSFirstAudioTimeoutError",
    "TTSFormatError",
    "TTSKeyExhaustedError",
    "TTSMalformedStreamError",
    "TTSNotReadyError",
    "TTSProviderAuthError",
    "TTSProviderError",
    "TTSProviderRateLimitError",
    "TTSProviderUnavailableError",
    "TTSStartedEvent",
    "TTSTotalTimeoutError",
    "extract_pcm_from_gemini_response",
]
