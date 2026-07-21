from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import soxr  # type: ignore[import-untyped]

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import AudioChunk


class VieNeuTtsProvider:
    source_sample_rate = 48_000

    def __init__(
        self,
        model_dir: Path,
        *,
        voice: str,
        output_sample_rate: int = 24_000,
        num_threads: int = 4,
        apply_watermark: bool = True,
        engine: Any | None = None,
    ) -> None:
        self._model_dir = model_dir
        self._voice = voice
        self._output_sample_rate = output_sample_rate
        self._num_threads = num_threads
        self._apply_watermark = apply_watermark
        self._engine = engine
        self._load_lock = threading.Lock()
        self._inference_lock = asyncio.Lock()

    async def prewarm(self) -> None:
        await asyncio.to_thread(self._load_engine)

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        del locale
        if not text.strip():
            return
        context.checkpoint()
        async with self._inference_lock:
            engine = await asyncio.to_thread(self._load_engine)
            stream = await asyncio.to_thread(
                engine.infer_stream,
                text,
                voice=self._voice,
                apply_watermark=self._apply_watermark,
            )
            resampler = soxr.ResampleStream(
                self.source_sample_rate,
                self._output_sample_rate,
                1,
                dtype="float32",
                quality="HQ",
            )
            sequence = 0
            while True:
                has_chunk, source = await asyncio.to_thread(self._next_chunk, stream)
                if not has_chunk:
                    break
                context.checkpoint()
                assert source is not None
                resampled = resampler.resample_chunk(source, last=False)
                pcm = self._float_to_pcm(resampled)
                if pcm:
                    yield AudioChunk(
                        sequence=sequence,
                        sample_rate=self._output_sample_rate,
                        encoding="pcm_s16le",
                        data=pcm,
                    )
                    sequence += 1
            tail = resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
            tail_pcm = self._float_to_pcm(tail)
            if tail_pcm:
                yield AudioChunk(
                    sequence=sequence,
                    sample_rate=self._output_sample_rate,
                    encoding="pcm_s16le",
                    data=tail_pcm,
                    final=True,
                )

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._load_lock:
            if self._engine is not None:
                return self._engine
            onnx_dir = self._model_dir / "onnx_int8"
            codec_dir = self._model_dir / "codec"
            if not onnx_dir.is_dir() or not codec_dir.is_dir():
                raise FileNotFoundError(
                    "VieNeu model is incomplete; run npm run models:prepare"
                )
            from vieneu import Vieneu  # type: ignore[import-untyped]

            self._engine = Vieneu(
                backbone_repo=str(self._model_dir),
                backend="onnx",
                precision="int8",
                onnx_dir=str(onnx_dir),
                codec_dir=str(codec_dir),
                threads=self._num_threads,
            )
            available = {voice_id for _, voice_id in self._engine.list_preset_voices()}
            if self._voice not in available:
                choices = sorted(available)
                raise ValueError(
                    f"VieNeu voice {self._voice!r} is unavailable; choose one of {choices}"
                )
            return self._engine

    @staticmethod
    def _next_chunk(stream: Iterator[Any]) -> tuple[bool, np.ndarray[Any, Any] | None]:
        try:
            return True, np.asarray(next(stream), dtype=np.float32)
        except StopIteration:
            return False, None

    @staticmethod
    def _float_to_pcm(samples: np.ndarray[Any, Any]) -> bytes:
        if samples.size == 0:
            return b""
        clipped = np.clip(samples, -1.0, 1.0)
        return bytes((clipped * 32767.0).astype("<i2").tobytes())
