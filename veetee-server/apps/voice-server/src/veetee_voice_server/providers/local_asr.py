from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import numpy as np
import soxr  # type: ignore[import-untyped]

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import Transcript
from veetee_voice_server.providers.local_runtime import ensure_sherpa_onnx_runtime


class SherpaZipformerAsrProvider:
    sample_rate = 16_000

    def __init__(
        self, model_dir: Path, *, num_threads: int = 4, recognizer: Any | None = None
    ) -> None:
        self._model_dir = model_dir
        self._num_threads = num_threads
        self._recognizer = recognizer
        self._load_lock = threading.Lock()
        self._inference_lock = asyncio.Lock()

    async def prewarm(self) -> None:
        await asyncio.to_thread(self._load_recognizer)

    async def transcribe_pcm(
        self,
        pcm_s16le: bytes,
        *,
        sample_rate: int,
        locale: str,
        context: OperationContext,
    ) -> Transcript:
        context.checkpoint()
        if not pcm_s16le or len(pcm_s16le) % 2:
            raise ValueError("ASR input must be non-empty PCM signed 16-bit little-endian")
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        if sample_rate != self.sample_rate:
            samples = soxr.resample(samples, sample_rate, self.sample_rate, quality="HQ")

        async with self._inference_lock:
            text = await asyncio.to_thread(self._decode, samples)
        context.checkpoint()
        return Transcript(text=text, locale=locale, stability=1.0)

    def _decode(self, samples: np.ndarray[Any, np.dtype[np.float32]]) -> str:
        recognizer = self._load_recognizer()
        stream = recognizer.create_stream()
        stream.accept_waveform(self.sample_rate, samples)
        recognizer.decode_stream(stream)
        return str(stream.result.text).strip()

    def _load_recognizer(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        with self._load_lock:
            if self._recognizer is not None:
                return self._recognizer
            required = {
                "encoder": self._model_dir / "encoder.int8.onnx",
                "decoder": self._model_dir / "decoder.onnx",
                "joiner": self._model_dir / "joiner.int8.onnx",
                "tokens": self._model_dir / "tokens.txt",
            }
            missing = [str(path) for path in required.values() if not path.is_file()]
            if missing:
                raise FileNotFoundError(f"Missing Sherpa Zipformer assets: {missing}")
            sherpa_onnx = ensure_sherpa_onnx_runtime()
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(required["encoder"]),
                decoder=str(required["decoder"]),
                joiner=str(required["joiner"]),
                tokens=str(required["tokens"]),
                num_threads=self._num_threads,
                decoding_method="greedy_search",
                provider="cpu",
            )
            return self._recognizer
