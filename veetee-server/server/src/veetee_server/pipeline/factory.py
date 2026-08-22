"""Default composition of the fake AI pipeline from runtime settings (M1.6).

The gateway builds one pipeline per processing turn through a factory so tests
can inject their own deterministic components via ``app.state.pipeline_factory``
and ``app.state.pacer_factory``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from veetee_server.audio.codec import (
    DOWNLINK_PCM_FORMAT,
    UPLINK_PCM_FORMAT,
    build_opus_decoder,
    build_opus_encoder,
)
from veetee_server.audio.pacer import PacketPacer
from veetee_server.config import Settings
from veetee_server.control_plane.catalog import allowed_agent_model_ids
from veetee_server.correction.engine import apply_asr_correction_hook
from veetee_server.domain.session import DeviceSession
from veetee_server.persistence.correction import CorrectionRepository
from veetee_server.prompt.providers import ContextProviderRegistry

from .asr import ASRNotReadyError, FakeASR, PhoWhisperRuntime
from .llm import FakeLLM, LLMNotReadyError, OmniRouteLLMAdapter, OmniRouteLLMRuntime
from .orchestrator import FakePipeline
from .segmenter import TTSSegmenterConfig, TTSTokenSegmenter
from .tts import (
    FakeTTS,
    GeminiTTSAdapter,
    GeminiTTSRuntime,
    TTSNotReadyError,
    VieNeuTTSAdapter,
    VieNeuTTSRuntime,
)
from .vad import BaseVADStream, FakeVAD, SileroVADRuntime, VADNotReadyError

if TYPE_CHECKING:
    from veetee_server.agents.snapshot import AgentRuntimeSnapshot

logger = logging.getLogger("veetee.pipeline")

PipelineFactory = Callable[[DeviceSession, Settings], FakePipeline]
PacerFactory = Callable[[Settings], PacketPacer]


def _snapshot_model_override(session: DeviceSession) -> str | None:
    """Returns a validated turn-scoped LLM model or the server default."""
    turn = session.current_turn
    snapshot: AgentRuntimeSnapshot | None = turn.snapshot if turn is not None else None
    model_id = snapshot.model_id.strip() if snapshot is not None else ""
    if not model_id:
        return None
    if model_id not in allowed_agent_model_ids():
        logger.warning(
            "agent_snapshot_model_rejected",
            extra={"context": {"session_id": str(session.id)}},
        )
        return None
    return model_id


def build_vad_stream(
    settings: Settings,
    vad_runtime: SileroVADRuntime | None = None,
) -> BaseVADStream | FakeVAD:
    """Builds a fresh VAD stream instance according to configured VAD provider."""
    if settings.vad_provider == "silero_onnx":
        if vad_runtime is None or not vad_runtime.is_ready:
            raise VADNotReadyError("Silero VAD is configured but its runtime is not ready")
        return vad_runtime.create_stream()
    return FakeVAD(
        speech_threshold=settings.pipeline_vad_speech_threshold,
        start_frames=settings.pipeline_vad_start_frames,
        end_silence_frames=settings.pipeline_vad_end_silence_frames,
        max_utterance_frames=settings.pipeline_max_utterance_frames,
    )


def build_fake_pipeline(
    session: DeviceSession,
    settings: Settings,
    vad_runtime: SileroVADRuntime | None = None,
    asr_runtime: PhoWhisperRuntime | None = None,
    llm_runtime: OmniRouteLLMRuntime | None = None,
    tts_runtime: GeminiTTSRuntime | None = None,
    vieneu_runtime: VieNeuTTSRuntime | None = None,
    correction_repository: CorrectionRepository | None = None,
    context_provider_registry: ContextProviderRegistry | None = None,
) -> FakePipeline:
    """Builds the default pipeline for a session."""
    vad = build_vad_stream(settings, vad_runtime=vad_runtime)

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
        llm = llm_runtime.create_adapter(
            model_override=_snapshot_model_override(session)
        )
    else:
        llm = FakeLLM()

    tts: FakeTTS | GeminiTTSAdapter | VieNeuTTSAdapter
    if settings.tts_provider == "gemini":
        if tts_runtime is None or not tts_runtime.is_ready:
            raise TTSNotReadyError("Gemini TTS is configured but its runtime is not ready")
        tts = tts_runtime.create_adapter()
    elif settings.tts_provider == "vieneu":
        if vieneu_runtime is None or not vieneu_runtime.is_ready:
            raise TTSNotReadyError("VieNeu TTS is configured but its runtime is not ready")
        tts = vieneu_runtime.create_adapter()
    else:
        tts = FakeTTS(chunks_per_sentence=settings.pipeline_tts_chunks_per_sentence)

    segmenter_config = TTSSegmenterConfig(
        first_min_chars=settings.tts_segment_first_min_chars,
        min_chars=settings.tts_segment_min_chars,
        max_chars=settings.tts_segment_max_chars,
        max_wait_seconds=settings.tts_segment_max_wait_seconds,
    )
    segmenter = TTSTokenSegmenter(config=segmenter_config)

    correction_hook = None
    context_hook = None
    if (
        correction_repository is not None
        and session.owner_user_id is not None
        and session.agent_id is not None
    ):
        owner_user_id = session.owner_user_id
        agent_id = session.agent_id

        async def correction_hook(text: str) -> tuple[str, dict[str, object]]:
            try:
                rules = await asyncio.to_thread(
                    correction_repository.list_active_rules, owner_user_id, agent_id
                )
            except Exception:
                logger.exception(
                    "correction_rules_unavailable",
                    extra={"context": {"session_id": str(session.id)}},
                )
                return text, {"status": "unavailable", "corrections_applied": 0}
            corrected, provenance = apply_asr_correction_hook(text, rules)
            return corrected, provenance

    if (
        context_provider_registry is not None
        and session.owner_user_id is not None
        and session.agent_id is not None
    ):
        owner_user_id = session.owner_user_id
        agent_id = session.agent_id

        async def context_hook(query: str) -> list[dict[str, object]]:
            results = await context_provider_registry.fetch_all(
                owner_user_id, agent_id, query, settings.context_provider_default_timeout_ms
            )
            return [
                {
                    "provider_type": result.provider_type,
                    "status": result.status,
                    "content": result.content,
                    "citations": result.citations,
                    "provenance": result.provenance,
                }
                for result in results
            ]

    return FakePipeline(
        decoder=build_opus_decoder(settings, pcm_format=UPLINK_PCM_FORMAT),
        encoder=build_opus_encoder(settings, pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=session.protocol_version,
        vad=vad,
        asr=asr,
        llm=llm,
        tts=tts,
        segmenter=segmenter,
        correction_hook=correction_hook,
        context_hook=context_hook,
    )


def build_downlink_pacer(settings: Settings) -> PacketPacer:
    """Builds the per-session downlink pacer from runtime settings."""
    return PacketPacer(max_drift_seconds=settings.audio_pacing_max_drift_ms / 1000.0)
