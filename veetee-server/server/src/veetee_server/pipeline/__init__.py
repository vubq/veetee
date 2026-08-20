"""Veetee fake AI pipeline module (M1.6).

Deterministic VAD -> ASR -> LLM -> TTS orchestration used as the realtime AI
pipeline harness until real model providers are integrated. Everything here
runs in-process with no network, models or secrets.
"""

from __future__ import annotations

from .asr import FakeASR
from .downlink import DownlinkItem, DownlinkKind, DownlinkQueue
from .events import (
    EventSink,
    PipelineEvent,
    SttEvent,
    TtsChunkEvent,
    TtsSentenceStartEvent,
    TtsStartEvent,
    TtsStopEvent,
)
from .factory import (
    PacerFactory,
    PipelineFactory,
    build_downlink_pacer,
    build_fake_pipeline,
)
from .framing import build_downlink_frame
from .llm import FakeLLM
from .orchestrator import FakePipeline, PipelineOutcome
from .tts import FakeTTS
from .vad import FakeVAD, VadEvent, VadEventKind

__all__ = [
    "DownlinkItem",
    "DownlinkKind",
    "DownlinkQueue",
    "EventSink",
    "FakeASR",
    "FakeLLM",
    "FakePipeline",
    "FakeTTS",
    "FakeVAD",
    "PacerFactory",
    "PipelineEvent",
    "PipelineFactory",
    "PipelineOutcome",
    "SttEvent",
    "TtsChunkEvent",
    "TtsSentenceStartEvent",
    "TtsStartEvent",
    "TtsStopEvent",
    "VadEvent",
    "VadEventKind",
    "build_downlink_frame",
    "build_downlink_pacer",
    "build_fake_pipeline",
]
