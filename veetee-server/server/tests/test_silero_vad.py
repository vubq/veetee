"""Unit and contract tests for Silero VAD adapter (M2.1)."""

import asyncio
from typing import Any

import pytest

from veetee_server.pipeline.vad import (
    InjectedVADEngine,
    SileroOnnxEngine,
    SileroVADConfig,
    SileroVADRuntime,
    VADAdmissionTimeoutError,
    VADEndReason,
    VadEventKind,
    VADModelError,
    VADNotReadyError,
    VADUtterance,
)


def _make_pcm_samples(sample_count: int, amplitude: int = 1000) -> bytes:
    """Helper to generate little-endian s16le PCM bytes."""
    import struct

    return struct.pack(f"<{sample_count}h", *([amplitude] * sample_count))


# --- Contract & Config Tests ---


def test_silero_vad_config_defaults_and_computed_props() -> None:
    config = SileroVADConfig()
    assert config.sample_rate == 16000
    assert config.sample_width == 2
    assert config.channels == 1
    assert config.window_samples == 512
    assert config.window_bytes == 1024
    assert config.threshold == 0.5
    assert config.neg_threshold == 0.35
    assert config.pre_roll_ms == 80
    assert config.pre_roll_samples == 1280  # 80 * 16
    assert config.min_speech_ms == 250
    assert config.min_speech_samples == 4000  # 250 * 16
    assert config.end_silence_ms == 150
    assert config.end_silence_samples == 2400  # 150 * 16
    assert config.max_utterance_ms == 12000
    assert config.max_utterance_samples == 192000  # 12000 * 16


def test_silero_vad_config_validation() -> None:
    with pytest.raises(ValueError, match="neg_threshold must not exceed threshold"):
        SileroVADConfig(threshold=0.3, neg_threshold=0.5)

    with pytest.raises(ValueError, match="max_utterance_ms must be at least min_speech_ms"):
        SileroVADConfig(min_speech_ms=500, max_utterance_ms=200)

    with pytest.raises(ValueError, match="threshold must be between 0.0 and 1.0"):
        SileroVADConfig(threshold=1.5)

    with pytest.raises(ValueError, match="16000 Hz"):
        SileroVADConfig(sample_rate=8000)

    with pytest.raises(ValueError, match="16-bit PCM"):
        SileroVADConfig(sample_width=4)

    with pytest.raises(ValueError, match="mono PCM"):
        SileroVADConfig(channels=2)


# --- Rechunking & Remainder Preservation Tests ---


@pytest.mark.asyncio
async def test_rechunk_arbitrary_frames_to_512_window() -> None:
    """Verifies arbitrary frame sizes (e.g. 960 samples / 60ms) rechunk to 512 samples."""
    processed_windows: list[int] = []

    def engine_handler(pcm_512: bytes, state: Any) -> tuple[float, Any]:
        assert len(pcm_512) == 1024  # 512 samples * 2 bytes
        processed_windows.append(len(pcm_512))
        return 0.1, state + 1

    engine = InjectedVADEngine(handler=engine_handler)
    config = SileroVADConfig()
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()
    processed_windows.clear()  # Clear warmup call record

    stream = runtime.create_stream()

    # Feed 1 frame of 960 samples (1920 bytes)
    # Expected: 1 window of 512 samples processed, remainder 448 samples buffered
    frame1 = _make_pcm_samples(960)
    await stream.process_pcm_async(frame1)
    assert len(processed_windows) == 1

    # Feed 2nd frame of 960 samples (1920 bytes)
    # Total accumulated: 896 + 1920 = 2816 bytes (1408 samples)
    # Expected: 2 windows of 512 samples processed, remainder 384 samples buffered
    frame2 = _make_pcm_samples(960)
    await stream.process_pcm_async(frame2)
    assert len(processed_windows) == 3

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_rejects_partial_pcm_sample() -> None:
    runtime = SileroVADRuntime(SileroVADConfig(), engine=InjectedVADEngine())
    await runtime.startup()
    stream = runtime.create_stream()

    with pytest.raises(ValueError, match="complete s16le mono samples"):
        await stream.process_pcm_async(b"\x00")

    await runtime.shutdown()


# --- Offsets & Pre-roll Tests ---


@pytest.mark.asyncio
async def test_speech_start_preroll_and_offsets() -> None:
    """Verifies speech start offset and 80ms pre-roll inclusion at speech boundary."""
    probs = [0.0, 0.0, 0.0, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0]
    engine = InjectedVADEngine(handler=probs)
    config = SileroVADConfig(pre_roll_ms=80, min_speech_ms=250, end_silence_ms=150)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()

    # Feed 16 windows of 512 samples
    for i in range(16):
        pcm = _make_pcm_samples(512, amplitude=5000 if i >= 3 else 0)
        events = await stream.process_pcm_async(pcm)
        if any(e.kind == VadEventKind.SPEECH_START for e in events):
            # Speech starts at window 3 (sample 1536).
            # Pre-roll 80ms = 1280 samples.
            # Expected speech start sample = 1536 - 1280 = 256
            assert stream._speech_start_sample == 256
            assert stream._speech_start_ms == (256 / 16000.0) * 1000.0

    utterance = stream.finish()
    assert isinstance(utterance, VADUtterance)
    assert utterance.start_sample == 256
    assert utterance.end_reason == VADEndReason.END_SILENCE
    # Pre-roll audio bytes included
    assert len(utterance.pcm_data) > 0

    await runtime.shutdown()


# --- Reset & Two-Stream Isolation Tests ---


@pytest.mark.asyncio
async def test_stream_reset_and_isolation() -> None:
    """Verifies that two streams operate independently without sharing state."""
    config = SileroVADConfig()
    observed_states: list[int] = []

    def engine_handler(_pcm: bytes, state: Any) -> tuple[float, Any]:
        observed_states.append(int(state))
        return 0.9, int(state) + 1

    runtime = SileroVADRuntime(config, engine=InjectedVADEngine(handler=engine_handler))
    await runtime.startup()
    observed_states.clear()

    stream1 = runtime.create_stream()
    stream2 = runtime.create_stream()

    pcm = _make_pcm_samples(512)

    events1 = await stream1.process_pcm_async(pcm)
    events2 = await stream2.process_pcm_async(pcm)

    assert any(e.kind == VadEventKind.SPEECH_START for e in events1)
    assert any(e.kind == VadEventKind.SPEECH_START for e in events2)
    assert observed_states == [0, 0]

    stream1.reset()
    assert stream1._state == "idle"
    assert stream2._state == "speaking"

    await runtime.shutdown()


# --- Load Once & Readiness Failure Tests ---


@pytest.mark.asyncio
async def test_load_once_and_readiness_failure() -> None:
    """Verifies runtime loads and warms once, and reports failure when unready."""
    config = SileroVADConfig()
    engine = InjectedVADEngine(handler=[0.1])
    runtime = SileroVADRuntime(config, engine=engine)

    assert not runtime.is_ready

    with pytest.raises(VADNotReadyError, match="not ready"):
        runtime.create_stream()

    await runtime.startup()
    assert runtime.is_ready

    # Second startup is a no-op
    await runtime.startup()
    assert runtime.is_ready

    await runtime.shutdown()
    assert not runtime.is_ready


@pytest.mark.asyncio
async def test_silero_onnx_engine_invalid_path() -> None:
    with pytest.raises(VADModelError, match="vad_model_path must be specified"):
        SileroOnnxEngine(model_path="")

    with pytest.raises(VADModelError, match="not found"):
        SileroOnnxEngine(model_path="/invalid/non_existent_model.onnx")

    runtime = SileroVADRuntime(SileroVADConfig(), model_path="/invalid/non_existent_model.onnx")
    with pytest.raises(VADModelError, match="not found"):
        await runtime.startup()


# --- Limiter, Admission Timeout & Cancellation Safety Tests ---


@pytest.mark.asyncio
async def test_concurrency_limiter_admission_timeout() -> None:
    """Verifies concurrency limiter rejects excess requests when admission timeout expires."""
    config = SileroVADConfig(max_concurrency=1, admission_timeout_seconds=0.05)

    def slow_engine(pcm: bytes, state: Any) -> tuple[float, Any]:
        import time

        time.sleep(0.2)
        return 0.5, state + 1

    engine = InjectedVADEngine(handler=slow_engine)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    pcm = _make_pcm_samples(512)

    task1 = asyncio.create_task(runtime.run_inference(pcm, 0))
    await asyncio.sleep(0.01)  # Ensure task1 acquires semaphore

    with pytest.raises(VADAdmissionTimeoutError, match="admission timed out"):
        await runtime.run_inference(pcm, 0)

    await task1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancellation_safety_no_permit_leak() -> None:
    """Cancellation retains the permit until the native worker actually exits."""
    config = SileroVADConfig(max_concurrency=1, admission_timeout_seconds=0.03)

    def blocking_engine(pcm: bytes, state: Any) -> tuple[float, Any]:
        import time

        time.sleep(0.15)
        return 0.5, state + 1

    engine = InjectedVADEngine(handler=blocking_engine)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    pcm = _make_pcm_samples(512)

    task = asyncio.create_task(runtime.run_inference(pcm, 0))
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    with pytest.raises(VADAdmissionTimeoutError):
        await runtime.run_inference(pcm, 0)

    await asyncio.sleep(0.15)
    res = await runtime.run_inference(pcm, 0)
    assert res[0] == 0.5

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stale_result_ignored_on_cancel() -> None:
    """Verifies resetting/cancelling a stream while inference is running drops stale results."""
    config = SileroVADConfig()

    def slow_engine(pcm: bytes, state: Any) -> tuple[float, Any]:
        import time

        time.sleep(0.05)
        return 0.9, state + 1

    engine = InjectedVADEngine(handler=slow_engine)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()
    pcm = _make_pcm_samples(512)

    proc_task = asyncio.create_task(stream.process_pcm_async(pcm))
    await asyncio.sleep(0.01)
    stream.cancel()

    events = await proc_task
    assert events == []  # Stale result ignored
    assert stream.finish() is None

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_waits_for_native_worker() -> None:
    def slow_engine(_pcm: bytes, state: Any) -> tuple[float, Any]:
        import time

        time.sleep(0.05)
        return 0.1, state

    runtime = SileroVADRuntime(
        SileroVADConfig(), engine=InjectedVADEngine(handler=slow_engine)
    )
    await runtime.startup()
    task = asyncio.create_task(runtime.run_inference(_make_pcm_samples(512), 0))
    await asyncio.sleep(0.01)

    await runtime.shutdown()

    assert not runtime._workers
    assert not runtime.is_ready
    assert await task == (0.1, 0)


# --- Boundary Scenarios: Silence, Noise, Short Burst, Continuous Speech ---


@pytest.mark.asyncio
async def test_boundary_pure_silence() -> None:
    engine = InjectedVADEngine(handler=[0.1] * 10)
    config = SileroVADConfig()
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()
    for _ in range(10):
        events = await stream.process_pcm_async(_make_pcm_samples(512))
        assert all(e.kind == VadEventKind.SILENCE for e in events)

    assert stream.finish() is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_boundary_short_noise_burst_rejected() -> None:
    """Short noise burst (< 250ms / 4000 samples / ~8 windows) is rejected."""
    # 2 speech windows (1024 samples = 64ms) < 250ms min speech, then silence
    probs = [0.0, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    engine = InjectedVADEngine(handler=probs)
    config = SileroVADConfig(min_speech_ms=250, end_silence_ms=150)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()
    for _ in range(len(probs)):
        await stream.process_pcm_async(_make_pcm_samples(512))

    # Should be rejected as noise burst
    assert stream.finish() is None
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_boundary_valid_short_speech() -> None:
    """Speech >= 250ms followed by 150ms silence yields valid utterance with END_SILENCE."""
    # 9 speech windows (4608 samples = 288ms > 250ms), then 5 silence windows
    probs = [0.0] + [0.9] * 9 + [0.1] * 5
    engine = InjectedVADEngine(handler=probs)
    config = SileroVADConfig(min_speech_ms=250, end_silence_ms=150)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()
    for _ in range(len(probs)):
        await stream.process_pcm_async(_make_pcm_samples(512))

    utterance = stream.finish()
    assert isinstance(utterance, VADUtterance)
    assert utterance.end_reason == VADEndReason.END_SILENCE

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_boundary_continuous_speech_max_utterance() -> None:
    """Continuous speech exceeding max_utterance_ms is force-closed with MAX_UTTERANCE."""
    # Max utterance 320ms (5120 samples = 10 windows of 512)
    probs = [0.9] * 20
    engine = InjectedVADEngine(handler=probs)
    config = SileroVADConfig(min_speech_ms=100, max_utterance_ms=320)
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    stream = runtime.create_stream()
    ended_event_found = False
    for _ in range(len(probs)):
        events = await stream.process_pcm_async(_make_pcm_samples(512))
        if any(e.kind == VadEventKind.SPEECH_END for e in events):
            ended_event_found = True

    assert ended_event_found
    utterance = stream.finish()
    assert isinstance(utterance, VADUtterance)
    assert utterance.end_reason == VADEndReason.MAX_UTTERANCE

    await runtime.shutdown()
