"""Tests for PhoWhisper ASR adapter, contract, runtime, concurrency, and pipeline integration."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.audio.queue import AudioQueueItem
from veetee_server.config import Settings
from veetee_server.domain.session import DeviceSession
from veetee_server.pipeline.asr import (
    ASRAdmissionTimeoutError,
    ASRNotReadyError,
    ASROversizedAudioError,
    ASRSegment,
    ASRTimeoutError,
    ASRTranscribeRequest,
    ASRValidationError,
    InjectedASREngine,
    PhoWhisperConfig,
    PhoWhisperRuntime,
    normalize_transcript,
)
from veetee_server.pipeline.events import PipelineEvent, SttEvent
from veetee_server.pipeline.factory import build_fake_pipeline
from veetee_server.pipeline.orchestrator import PipelineOutcome
from veetee_server.pipeline.vad import VADUtterance


class RecordingSink:
    """Event sink recording pipeline events for verification."""

    def __init__(self) -> None:
        self.events: list[PipelineEvent] = []

    async def emit(self, event: PipelineEvent) -> None:
        self.events.append(event)


def _make_pcm(duration_seconds: float = 1.0, frequency_hz: float = 440.0) -> bytes:
    """Generates 16 kHz mono s16le PCM sine wave bytes."""
    import math
    import struct

    num_samples = int(duration_seconds * 16000)
    samples = []
    for i in range(num_samples):
        val = int(16000 * math.sin(2 * math.pi * frequency_hz * i / 16000))
        samples.append(val)
    return struct.pack(f"<{len(samples)}h", *samples)


def test_normalize_transcript() -> None:
    assert normalize_transcript("") == ""
    assert normalize_transcript("   ") == ""
    assert normalize_transcript("  Xin   chào   các  bạn  ") == "Xin chào các bạn"
    assert (
        normalize_transcript("  Dòng 1 \n  Dòng  2  \n\n  Dòng 3 ")
        == "Dòng 1 Dòng 2 Dòng 3"
    )


def test_phowhisper_config_validation() -> None:
    cfg = PhoWhisperConfig()
    assert cfg.model_id == "mad1999/pho-whisper-small-ct2"
    assert cfg.device == "cuda"
    assert cfg.compute_type == "float16"

    with pytest.raises(ValueError, match="model_id must be a non-empty string"):
        PhoWhisperConfig(model_id="")

    with pytest.raises(ValueError, match="max_concurrency must be positive"):
        PhoWhisperConfig(max_concurrency=0)

    with pytest.raises(ValueError, match="admission_timeout_seconds must be positive"):
        PhoWhisperConfig(admission_timeout_seconds=0.0)

    with pytest.raises(ValueError, match="total_timeout_seconds must be positive"):
        PhoWhisperConfig(total_timeout_seconds=-1.0)

    with pytest.raises(ValueError, match="max_audio_seconds must be positive"):
        PhoWhisperConfig(max_audio_seconds=0.0)


def test_settings_asr_validation() -> None:
    s = Settings(asr_provider="pho_whisper")
    assert s.asr_provider == "pho_whisper"
    assert s.asr_model_id == "mad1999/pho-whisper-small-ct2"

    with pytest.raises(
        ValueError, match="asr_total_timeout_seconds must be strictly greater"
    ):
        Settings(asr_admission_timeout_seconds=5.0, asr_total_timeout_seconds=2.0)


@pytest.mark.asyncio
async def test_pcm_validation_and_preprocessing() -> None:
    cfg = PhoWhisperConfig(max_audio_seconds=2.0)
    engine = InjectedASREngine()
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        # Empty PCM
        with pytest.raises(ASRValidationError, match="PCM data cannot be empty"):
            await runtime.transcribe_async(b"")

        # Incomplete sample (odd length)
        with pytest.raises(
            ASRValidationError, match="PCM data length must be a multiple of 2"
        ):
            await runtime.transcribe_async(b"\x00\x01\x02")

        # Oversized audio
        big_pcm = b"\x00\x00" * int(16000 * 2.5)  # 2.5s > 2.0s
        with pytest.raises(
            ASROversizedAudioError, match="Audio duration 2.50s exceeds maximum"
        ):
            await runtime.transcribe_async(big_pcm)

        # Invalid request type
        with pytest.raises(
            ASRValidationError, match="Unsupported request type"
        ):
            await runtime.transcribe_async(12345)  # type: ignore[arg-type]

        # Valid ASRTranscribeRequest
        req = ASRTranscribeRequest(pcm_data=_make_pcm(0.5), language="en")
        res1 = await runtime.transcribe_async(req)
        assert res1.raw_text == "Xin chào Veetee"
        assert res1.normalized_text == "Xin chào Veetee"
        assert res1.language == "en"

        # Valid VADUtterance
        utt = VADUtterance(
            start_sample=0,
            end_sample=8000,
            start_ms=0.0,
            end_ms=500.0,
            pcm_data=_make_pcm(0.5),
        )
        res2 = await runtime.transcribe_async(utt)
        assert res2.normalized_text == "Xin chào Veetee"

        # All silence / zeros
        silence_pcm = b"\x00" * 3200
        res_silence = await runtime.transcribe_async(silence_pcm)
        assert res_silence.raw_text == ""
        assert res_silence.normalized_text == ""
        assert res_silence.provider_metadata.get("silence") is True
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_load_once_and_readiness_failure() -> None:
    class UnreadyEngine(InjectedASREngine):
        @property
        def is_ready(self) -> bool:
            return False

    runtime = PhoWhisperRuntime(config=PhoWhisperConfig(), engine=UnreadyEngine())

    # Calling transcribe when unready
    with pytest.raises(ASRNotReadyError, match="PhoWhisper runtime is not ready"):
        await runtime.transcribe_async(_make_pcm(0.2))

    # Startup fails deterministically without network or model lookup.
    with pytest.raises(ASRNotReadyError):
        await runtime.startup()

    assert not runtime.is_ready


@pytest.mark.asyncio
async def test_concurrency_and_admission_timeout() -> None:
    cfg = PhoWhisperConfig(max_concurrency=1, admission_timeout_seconds=0.1)
    engine = InjectedASREngine(delay_seconds=0.5)
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        pcm = _make_pcm(0.2)
        task1 = asyncio.create_task(runtime.transcribe_async(pcm))
        await asyncio.sleep(0.02)

        # Second request fails admission timeout because worker 1 takes 0.5s > 0.1s timeout
        with pytest.raises(ASRAdmissionTimeoutError):
            await runtime.transcribe_async(pcm)

        res1 = await task1
        assert res1.normalized_text == "Xin chào Veetee"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancellation_and_native_permit_release() -> None:
    cfg = PhoWhisperConfig(max_concurrency=1, admission_timeout_seconds=1.0)
    engine = InjectedASREngine(delay_seconds=0.3)
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        pcm = _make_pcm(0.2)
        task1 = asyncio.create_task(runtime.transcribe_async(pcm))
        await asyncio.sleep(0.02)

        # Cancel task 1 while running
        task1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task1

        # Wait until background worker releases the permit (approx 0.3s total)
        await asyncio.sleep(0.35)

        # Next request should succeed without deadlock or permit leak
        res2 = await runtime.transcribe_async(pcm)
        assert res2.normalized_text == "Xin chào Veetee"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_total_timeout() -> None:
    cfg = PhoWhisperConfig(total_timeout_seconds=0.1, admission_timeout_seconds=1.0)
    engine = InjectedASREngine(delay_seconds=0.3)
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        pcm = _make_pcm(0.2)
        with pytest.raises(ASRTimeoutError, match="ASR total timeout exceeded"):
            await runtime.transcribe_async(pcm)
        assert not runtime.is_ready
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_wait_and_lifecycle_lock() -> None:
    cfg = PhoWhisperConfig(max_concurrency=1)
    engine = InjectedASREngine(delay_seconds=0.2)
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    pcm = _make_pcm(0.2)
    task1 = asyncio.create_task(runtime.transcribe_async(pcm))
    await asyncio.sleep(0.02)

    # Trigger shutdown while request is running
    shutdown_task = asyncio.create_task(runtime.shutdown())

    # New requests during shutdown should fail immediately with ASRNotReadyError
    with pytest.raises(ASRNotReadyError):
        await runtime.transcribe_async(pcm)

    res1 = await task1
    assert res1.normalized_text == "Xin chào Veetee"

    await shutdown_task
    assert not runtime.is_ready


@pytest.mark.asyncio
async def test_two_request_isolation() -> None:
    def _custom_transcribe(pcm: Any, lang: str) -> tuple[str, list[ASRSegment], dict[str, Any]]:
        if len(pcm) > 4000:
            return "  Câu   dài  ", [], {"custom": 1}
        return "Câu ngắn", [], {"custom": 2}

    cfg = PhoWhisperConfig(max_concurrency=2)
    engine = InjectedASREngine(transcribe_fn=_custom_transcribe)
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        pcm1 = _make_pcm(0.5)  # 8000 samples
        pcm2 = _make_pcm(0.1)  # 1600 samples

        res1, res2 = await asyncio.gather(
            runtime.transcribe_async(pcm1),
            runtime.transcribe_async(pcm2),
        )

        assert res1.raw_text == "  Câu   dài  "
        assert res1.normalized_text == "Câu dài"
        assert res1.provider_metadata.get("custom") == 1

        assert res2.raw_text == "Câu ngắn"
        assert res2.normalized_text == "Câu ngắn"
        assert res2.provider_metadata.get("custom") == 2
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_pipeline_integration_phowhisper() -> None:
    cfg = PhoWhisperConfig()
    engine = InjectedASREngine(
        transcribe_fn=lambda pcm, lang: ("   Chào   Veetee  ", [], {"test": True})
    )
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        settings = Settings(
            asr_provider="pho_whisper",
            pipeline_vad_speech_threshold=0.0,  # force speech detection
            pipeline_vad_start_frames=1,
        )
        session = DeviceSession(device_id="d1", client_id="c1")
        session.accept()
        await session.start_turn()

        pipeline = build_fake_pipeline(session, settings, asr_runtime=runtime)

        # Enqueue some PCM frames to be processed
        pcm_frame = b"\x00\x10" * 480  # 60ms frame
        await session.ingress_queue.put(
            AudioQueueItem(payload=pcm_frame, generation=session.ingress_queue.generation)
        )
        session.begin_processing()

        sink = RecordingSink()
        outcome = await pipeline.run(session, sink)

        assert outcome == PipelineOutcome.COMPLETED
        stt_events = [e for e in sink.events if isinstance(e, SttEvent)]
        assert len(stt_events) == 1
        assert stt_events[0].text == "Chào Veetee"
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_pipeline_integration_empty_transcript() -> None:
    cfg = PhoWhisperConfig()
    engine = InjectedASREngine(
        transcribe_fn=lambda pcm, lang: ("   ", [], {"empty": True})
    )
    runtime = PhoWhisperRuntime(config=cfg, engine=engine)
    await runtime.startup()

    try:
        settings = Settings(
            asr_provider="pho_whisper",
            pipeline_vad_speech_threshold=0.0,
            pipeline_vad_start_frames=1,
        )
        session = DeviceSession(device_id="d1", client_id="c1")
        session.accept()
        await session.start_turn()

        pipeline = build_fake_pipeline(session, settings, asr_runtime=runtime)
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=b"\x00\x10" * 480, generation=session.ingress_queue.generation
            )
        )
        session.begin_processing()

        sink = RecordingSink()
        outcome = await pipeline.run(session, sink)

        assert outcome == PipelineOutcome.NO_UTTERANCE
        stt_events = [e for e in sink.events if isinstance(e, SttEvent)]
        assert len(stt_events) == 0
    finally:
        await runtime.shutdown()


def test_app_readiness_phowhisper() -> None:
    settings = Settings(
        environment="test",
        asr_provider="pho_whisper",
        asr_model_id="veetee-test-model-not-present-locally",
        asr_local_files_only=True,
    )
    app = create_app(settings)
    client = TestClient(app)

    # Lifespan startup runs on TestClient context
    with client:
        # A deliberately absent local-only model keeps this readiness test deterministic.
        res = client.get("/readyz")
        assert res.status_code == 503
        body = res.json()
        assert body["status"] == "not_ready"
        assert body["reason"] == "asr_runtime_not_ready"
