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
from veetee_voice_server.conversation.sentence_chunker import TtsTextChunkingPolicy
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
        assert kwargs["style"] == "tu_nhien"
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


async def test_vieneu_resets_normalized_metrics_before_a_new_request() -> None:
    class FailingNormalizedEngine:
        normalized_chunk_count = 3
        normalized_chunk_characters = 240
        normalized_chunk_max_characters = 96
        internal_inference_start_count = 3

        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text, kwargs
            raise RuntimeError("normalization failed")

    engine = FailingNormalizedEngine()
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        engine=engine,
    )

    with pytest.raises(RuntimeError, match="normalization failed"):
        async for _ in provider.synthesize("Lượt mới", "vi-VN", context()):
            pass

    assert engine.normalized_chunk_count == 0
    assert engine.normalized_chunk_characters == 0
    assert engine.normalized_chunk_max_characters == 0
    assert engine.internal_inference_start_count == 0
    assert provider._normalized_chunk_count == 0
    assert provider._normalized_chunk_characters == 0
    assert provider._normalized_chunk_max_characters == 0
    assert provider._internal_inference_start_count == 0


async def test_vieneu_distinguishes_normalized_chunks_from_inference_starts() -> None:
    class SplitMetricEngine:
        normalized_chunk_count = 0
        normalized_chunk_characters = 0
        normalized_chunk_max_characters = 0
        internal_inference_start_count = 0

        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text, kwargs
            self.normalized_chunk_count = 3
            self.normalized_chunk_characters = 240
            self.normalized_chunk_max_characters = 96
            self.internal_inference_start_count = 1
            yield np.zeros(4_800, dtype=np.float32)

    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        engine=SplitMetricEngine(),
    )

    chunks = [
        chunk
        async for chunk in provider.synthesize("Lượt mới", "vi-VN", context())
    ]

    assert chunks
    assert provider._normalized_chunk_count == 3
    assert provider._internal_inference_start_count == 1


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
        style="doc_truyen",
        speed=1.5,
        volume=1.25,
    )

    assert profile._inference_lock is base._inference_lock
    assert profile._turn_lock is base._turn_lock
    assert profile._turn_scope_active is base._turn_scope_active
    assert profile._worker_lock is base._worker_lock
    assert profile._style == "doc_truyen"
    assert profile.quality_warnings == (
        "postprocess_rate_starvation_risk",
        "amplification_clipping_risk",
    )


async def test_vieneu_native_profile_keeps_natural_clause_conditioning() -> None:
    class NativeProfileEngine:
        def __init__(self) -> None:
            self.use_ref_codes: bool | None = None

        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text
            self.use_ref_codes = kwargs["use_ref_codes"]
            yield np.zeros(4_800, dtype=np.float32)

    engine = NativeProfileEngine()
    base = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        backend="native",
        native_model_dir=Path("native-model"),
        native_library_path=Path("libvieneu-tts.so"),
        engine=engine,
    )
    profile = base.with_profile(voice="Trúc Ly", speed=1.0)
    chunks = [
        chunk
        async for chunk in profile.synthesize("Xin chào", "vi-VN", context())
    ]

    assert chunks
    assert engine.use_ref_codes is True
    assert base.preferred_text_chunk_characters == 48
    assert profile.preferred_text_chunk_characters == 48
    assert base.maximum_text_chunk_characters == 72
    assert profile.maximum_text_chunk_characters == 72
    assert base.initial_text_chunk_characters == 24
    assert profile.initial_maximum_text_chunk_characters == 40
    assert profile._backend == "native"
    assert profile.text_chunking_policy == TtsTextChunkingPolicy(
        mode="sentence_bounded",
        emergency_max_characters=72,
        sentence_batch_max_characters=72,
    )
    assert profile._native_model_dir == Path("native-model")
    assert profile._native_library_path == Path("libvieneu-tts.so")
    assert profile._native_use_ref_codes is True


async def test_vieneu_native_can_disable_reference_codes_for_quality_comparison() -> None:
    class NativeProfileEngine:
        def __init__(self) -> None:
            self.use_ref_codes: bool | None = None

        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text
            self.use_ref_codes = kwargs["use_ref_codes"]
            yield np.zeros(4_800, dtype=np.float32)

    engine = NativeProfileEngine()
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        backend="native",
        native_model_dir=Path("native-model"),
        native_library_path=Path("libvieneu-tts.so"),
        native_use_ref_codes=False,
        engine=engine,
    )

    chunks = [
        chunk
        async for chunk in provider.synthesize("Xin chào", "vi-VN", context())
    ]

    assert chunks
    assert engine.use_ref_codes is False


async def test_vieneu_native_honors_requested_speed_without_feedback_slowdown() -> None:
    class SlowNativeBatchEngine:
        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text, kwargs
            sleep(0.12)
            yield np.zeros(48_000, dtype=np.float32)

    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        speed=1.5,
        backend="native",
        native_model_dir=Path("native-model"),
        native_library_path=Path("libvieneu-tts.so"),
        native_realtime_headroom=1.15,
        engine=SlowNativeBatchEngine(),
    )

    chunks = [
        chunk
        async for chunk in provider.synthesize("Xin chào", "vi-VN", context())
    ]

    assert chunks
    duration = len(b"".join(chunk.data for chunk in chunks)) / 2 / 24_000
    assert 0.6 < duration < 0.75
    assert provider._speed == 1.5


async def test_vieneu_cancelled_native_work_cannot_overlap_the_next_batch() -> None:
    class SlowNativeEngine:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0

        def infer_stream(
            self, text: str, **kwargs: Any
        ) -> Iterator[np.ndarray[Any, Any]]:
            del text, kwargs
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            try:
                sleep(0.08)
            finally:
                self.active -= 1
            yield np.zeros(4_800, dtype=np.float32)

    engine = SlowNativeEngine()
    provider = VieNeuTtsProvider(
        Path("unused"),
        voice="Trúc Ly",
        backend="native",
        native_model_dir=Path("native-model"),
        native_library_path=Path("libvieneu-tts.so"),
        engine=engine,
    )
    first_stream = provider.synthesize("Lượt cũ", "vi-VN", context())
    first = asyncio.create_task(anext(first_stream))
    await asyncio.sleep(0.01)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)

    second_stream = provider.synthesize("Lượt mới", "vi-VN", context())
    second = await asyncio.wait_for(anext(second_stream), timeout=1.0)
    await second_stream.aclose()

    assert second.data
    assert engine.maximum_active == 1


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
