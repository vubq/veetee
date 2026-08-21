"""Voice Activity Detection (VAD) module supporting Fake VAD and Silero ONNX VAD."""

from .contract import (
    BaseVADStream,
    SileroVADConfig,
    VADAdmissionTimeoutError,
    VADEndReason,
    VADEngineProtocol,
    VADError,
    VADModelError,
    VADNotReadyError,
    VADSampleOffset,
    VADUtterance,
)
from .fake import FakeVAD, SpeechSegment, VadEvent, VadEventKind, pcm_rms
from .silero import (
    InjectedVADEngine,
    SileroOnnxEngine,
    SileroVADRuntime,
    SileroVADStream,
)

__all__ = [
    "BaseVADStream",
    "FakeVAD",
    "InjectedVADEngine",
    "SileroOnnxEngine",
    "SileroVADConfig",
    "SileroVADRuntime",
    "SileroVADStream",
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
    "pcm_rms",
]
