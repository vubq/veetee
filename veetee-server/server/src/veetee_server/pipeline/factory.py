"""Default composition of the fake AI pipeline from runtime settings (M1.6).

The gateway builds one pipeline per processing turn through a factory so tests
can inject their own deterministic components via ``app.state.pipeline_factory``
and ``app.state.pacer_factory``.
"""

from __future__ import annotations

from collections.abc import Callable

from veetee_server.audio.codec import (
    DOWNLINK_PCM_FORMAT,
    UPLINK_PCM_FORMAT,
    FakeOpusDecoder,
    FakeOpusEncoder,
)
from veetee_server.audio.pacer import PacketPacer
from veetee_server.config import Settings
from veetee_server.domain.session import DeviceSession

from .asr import ASRNotReadyError, FakeASR, PhoWhisperRuntime
from .llm import FakeLLM, LLMNotReadyError, OmniRouteLLMAdapter, OmniRouteLLMRuntime
from .orchestrator import FakePipeline
from .tts import FakeTTS
from .vad import BaseVADStream, FakeVAD, SileroVADRuntime, VADNotReadyError

PipelineFactory = Callable[[DeviceSession, Settings], FakePipeline]
PacerFactory = Callable[[Settings], PacketPacer]


def build_fake_pipeline(
    session: DeviceSession,
    settings: Settings,
    vad_runtime: SileroVADRuntime | None = None,
    asr_runtime: PhoWhisperRuntime | None = None,
    llm_runtime: OmniRouteLLMRuntime | None = None,
) -> FakePipeline:
    """Builds the default pipeline for a session."""
    vad: FakeVAD | BaseVADStream
    if settings.vad_provider == "silero_onnx":
        if vad_runtime is None or not vad_runtime.is_ready:
            raise VADNotReadyError("Silero VAD is configured but its runtime is not ready")
        vad = vad_runtime.create_stream()
    else:
        vad = FakeVAD(
            speech_threshold=settings.pipeline_vad_speech_threshold,
            start_frames=settings.pipeline_vad_start_frames,
            end_silence_frames=settings.pipeline_vad_end_silence_frames,
            max_utterance_frames=settings.pipeline_max_utterance_frames,
        )

    asr: FakeASR | PhoWhisperRuntime
    if settings.asr_provider == "pho_whisper":
        if asr_runtime is None or not asr_runtime.is_ready:
            raise ASRNotReadyError("PhoWhisper ASR is configured but its runtime is not ready")
        asr = asr_runtime
    else:
        asr = FakeASR()

    llm: FakeLLM | OmniRouteLLMAdapter
    if settings.llm_provider == "omniroute":
        if llm_runtime is None or not llm_runtime.is_ready:
            raise LLMNotReadyError("OmniRoute LLM is configured but its runtime is not ready")
        llm = llm_runtime.create_adapter()
    else:
        llm = FakeLLM()

    return FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=session.protocol_version,
        vad=vad,
        asr=asr,
        llm=llm,
        tts=FakeTTS(chunks_per_sentence=settings.pipeline_tts_chunks_per_sentence),
    )


def build_downlink_pacer(settings: Settings) -> PacketPacer:
    """Builds the per-session downlink pacer from runtime settings."""
    return PacketPacer(max_drift_seconds=settings.audio_pacing_max_drift_ms / 1000.0)
