from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import Any

import numpy as np
import pytest

from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
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
