from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    OperationDeadlineExceededError,
)
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import (
    VieNeuTtsProvider,
    _configure_stream_leadin,
)
from veetee_voice_server.providers.silero_vad import CHUNK_MS, SileroVadSession

pytestmark = pytest.mark.asyncio


def context() -> OperationContext:
    return OperationContext("session", "turn", 1, CancellationToken(), monotonic() + 5)


class FakeAsrResult:
    text = "XIN CHÀO VEETEE"


class FakeAsrStream:
    def __init__(self) -> None:
        self.result = FakeAsrResult()

    def accept_waveform(self, sample_rate: int, samples: np.ndarray[Any, Any]) -> None:
        assert sample_rate == 16_000
        assert samples.dtype == np.float32


class FakeRecognizer:
    def create_stream(self) -> FakeAsrStream:
        return FakeAsrStream()

    def decode_stream(self, stream: FakeAsrStream) -> None:
        del stream


async def test_zipformer_provider_preserves_vietnamese_text() -> None:
    provider = SherpaZipformerAsrProvider(Path("unused"), recognizer=FakeRecognizer())
    transcript = await provider.transcribe_pcm(
        b"\x01\x00" * 1_600,
        sample_rate=16_000,
        locale="vi-VN",
        context=context(),
    )
    assert transcript.text == "XIN CHÀO VEETEE"
    assert transcript.locale == "vi-VN"
    assert transcript.confidence is None
    assert transcript.stability is None


class SlowRecognizer(FakeRecognizer):
    def decode_stream(self, stream: FakeAsrStream) -> None:
        sleep(0.08)
        super().decode_stream(stream)


async def test_zipformer_deadline_returns_without_overlapping_native_decode() -> None:
    provider = SherpaZipformerAsrProvider(
        Path("unused"),
        recognizer=SlowRecognizer(),
    )
    deadline_context = OperationContext(
        "session",
        "slow-asr",
        1,
        CancellationToken(),
        monotonic() + 0.01,
    )

    with pytest.raises(OperationDeadlineExceededError):
        await provider.transcribe_pcm(
            b"\x01\x00" * 1_600,
            sample_rate=16_000,
            locale="vi-VN",
            context=deadline_context,
        )

    # Native ONNX work cannot be force-killed, but the worker-owned lock stays
    # held until it exits so a later decode cannot overlap the stale call.
    await asyncio.sleep(0.09)


class FakeTtsEngine:
    def infer_stream(self, text: str, **kwargs: Any) -> Iterator[np.ndarray[Any, Any]]:
        assert text == "Xin chào"
        assert kwargs["voice"] == "Ngọc Linh"
        yield np.full(4_800, 0.25, dtype=np.float32)


async def test_vieneu_provider_streams_resampled_pcm() -> None:
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Ngọc Linh",
        output_sample_rate=24_000,
        engine=FakeTtsEngine(),
    )
    chunks = [chunk async for chunk in provider.synthesize("Xin chào", "vi-VN", context())]
    assert chunks
    assert chunks[0].sample_rate == 24_000
    assert chunks[0].encoding == "pcm_s16le"
    assert len(b"".join(chunk.data for chunk in chunks)) > 4_000


async def test_vieneu_stream_leadin_is_bounded_and_version_checked() -> None:
    module = SimpleNamespace(_STREAM_LEADIN_FRAMES=4)
    _configure_stream_leadin(module, 16)
    assert module._STREAM_LEADIN_FRAMES == 16

    with pytest.raises(RuntimeError, match=r"3\.2\.3"):
        _configure_stream_leadin(SimpleNamespace(), 16)
    with pytest.raises(ValueError, match=r"4 to 25"):
        VieNeuTtsProvider(Path("unused"), voice="Trúc Ly", stream_leadin_frames=3)


class ToneTtsEngine:
    def infer_stream(self, text: str, **kwargs: Any) -> Iterator[np.ndarray[Any, Any]]:
        del text, kwargs
        sample_rate = 48_000
        samples = np.arange(sample_rate * 2, dtype=np.float32)
        yield (0.25 * np.sin(2 * np.pi * 220 * samples / sample_rate)).astype(np.float32)


async def test_vieneu_provider_accelerates_tempo_without_raising_pitch() -> None:
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        speed=1.2,
        output_sample_rate=24_000,
        engine=ToneTtsEngine(),
    )

    chunks = [chunk async for chunk in provider.synthesize("Xin chào", "vi-VN", context())]
    pcm = np.frombuffer(b"".join(chunk.data for chunk in chunks), dtype="<i2")
    duration = len(pcm) / 24_000
    spectrum = np.abs(np.fft.rfft(pcm.astype(np.float32)))
    peak_hz = np.fft.rfftfreq(len(pcm), 1 / 24_000)[int(np.argmax(spectrum))]

    assert 1.55 < duration < 1.75
    assert peak_hz == pytest.approx(220, abs=3)


async def test_vieneu_profile_views_share_inference_lock_and_report_risky_postprocessing() -> None:
    base = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        engine=ToneTtsEngine(),
    )
    profile = base.with_profile(
        voice="Trúc Ly",
        speed=1.5,
        volume=1.25,
    )

    assert profile._inference_lock is base._inference_lock
    assert profile.quality_warnings == (
        "postprocess_rate_starvation_risk",
        "amplification_clipping_risk",
    )


async def test_vieneu_counts_samples_clipped_by_profile_amplification() -> None:
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        volume=1.5,
        engine=FakeTtsEngine(),
    )
    samples = np.array([-0.9, -0.5, 0.5, 0.9], dtype=np.float32)

    pcm = np.frombuffer(provider._float_to_pcm(samples), dtype="<i2")

    assert provider._conversion_samples == 4
    assert provider._conversion_clipped_samples == 2
    assert pcm.tolist() == [-32767, -24575, 24575, 32767]


class FakeVadModel:
    def __init__(self, probabilities: list[float]) -> None:
        self._probabilities = iter(probabilities)

    def predict(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        state: np.ndarray[Any, np.dtype[np.float32]],
        previous_context: np.ndarray[Any, np.dtype[np.float32]],
    ) -> tuple[float, np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        del samples
        return next(self._probabilities), state, previous_context


async def test_silero_endpoint_requires_configured_silence_not_one_quiet_chunk() -> None:
    silence_chunks = (400 + CHUNK_MS - 1) // CHUNK_MS
    model = FakeVadModel([0.9, *([0.1] * silence_chunks)])
    session = SileroVadSession(model, min_silence_ms=400)
    results = session.process(b"\0" * 1_024 * (silence_chunks + 1))

    assert results[0].speech_started is True
    assert results[1].speech_ended is False
    assert results[-1].speech_ended is True
    assert results[-1].endpoint_reason == "silence"


async def test_silero_uses_shorter_endpoint_only_after_sustained_confident_speech() -> None:
    speech_chunks = 20
    fast_silence_chunks = 10
    model = FakeVadModel(
        [*([0.9] * speech_chunks), *([0.05] * fast_silence_chunks)]
    )
    session = SileroVadSession(
        model,
        min_silence_ms=400,
        fast_silence_ms=320,
        fast_endpoint_min_speech_ms=640,
        quiet_probability=0.15,
    )

    results = session.process(
        b"\0" * 1_024 * (speech_chunks + fast_silence_chunks)
    )

    assert all(not result.speech_ended for result in results[:-1])
    assert results[-1].speech_ended is True
    assert results[-1].endpoint_reason == "confident_quiet"

async def test_silero_endpoint_can_disable_fixed_utterance_duration() -> None:
    speech_chunks = 4
    silence_chunks = (400 + CHUNK_MS - 1) // CHUNK_MS
    model = FakeVadModel([*([0.9] * speech_chunks), *([0.1] * silence_chunks)])
    session = SileroVadSession(model, min_silence_ms=400, max_speech_seconds=0)
    results = session.process(b"\0" * 1_024 * (speech_chunks + silence_chunks))

    assert all(not result.speech_ended for result in results[:speech_chunks])
    assert results[-1].speech_ended is True
