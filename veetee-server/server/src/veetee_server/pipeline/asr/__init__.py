"""ASR providers, contracts, and runtime implementations."""

from .contract import (
    ASRAdmissionTimeoutError,
    ASREngineProtocol,
    ASRError,
    ASRModelError,
    ASRNotReadyError,
    ASROversizedAudioError,
    ASRProvider,
    ASRResult,
    ASRSegment,
    ASRTimeoutError,
    ASRTranscribeRequest,
    ASRValidationError,
    PhoWhisperConfig,
    normalize_transcript,
)
from .fake import FakeASR
from .phowhisper import InjectedASREngine, PhoWhisperCtranslateEngine, PhoWhisperRuntime

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
    "FakeASR",
    "InjectedASREngine",
    "PhoWhisperConfig",
    "PhoWhisperCtranslateEngine",
    "PhoWhisperRuntime",
    "normalize_transcript",
]
