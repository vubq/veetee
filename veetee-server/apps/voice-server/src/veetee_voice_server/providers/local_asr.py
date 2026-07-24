from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from time import monotonic
from typing import Any

import numpy as np
import soxr  # type: ignore[import-untyped]
import structlog

from veetee_voice_server.conversation.cancellation import OperationContext, await_operation
from veetee_voice_server.conversation.types import Transcript
from veetee_voice_server.providers.local_runtime import ensure_sherpa_onnx_runtime

logger = structlog.get_logger(__name__)


class SherpaZipformerAsrProvider:
    sample_rate = 16_000

    def __init__(
        self, model_dir: Path, *, num_threads: int = 4, recognizer: Any | None = None
    ) -> None:
        self._model_dir = model_dir
        self._num_threads = num_threads
        self._recognizer = recognizer
        self._load_lock = threading.Lock()
        self._decode_lock = threading.Lock()
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
        started_at = monotonic()
        samples = await await_operation(
            asyncio.to_thread(self._prepare_samples, pcm_s16le, sample_rate),
            context,
        )

        queued_at = monotonic()
        async with self._inference_lock:
            queue_ms = (monotonic() - queued_at) * 1_000
            text = await await_operation(
                asyncio.to_thread(self._decode_serialized, samples),
                context,
            )
        context.checkpoint()
        logger.info(
            "zipformer_asr_completed",
            turn_id=context.turn_id,
            audio_ms=round(samples.size * 1_000 / self.sample_rate, 1),
            queue_ms=round(queue_ms, 1),
            duration_ms=round((monotonic() - started_at) * 1_000, 1),
            transcript_characters=len(text),
            confidence_available=False,
        )
        # The pinned offline Zipformer API does not expose calibrated confidence
        # or partial stability. Keep both null instead of inventing certainty.
        return Transcript(text=text, locale=locale)

    def _prepare_samples(
        self, pcm_s16le: bytes, sample_rate: int
    ) -> np.ndarray[Any, np.dtype[np.float32]]:
        samples = np.frombuffer(pcm_s16le, dtype="<i2").astype(np.float32) / 32768.0
        if sample_rate != self.sample_rate:
            samples = soxr.resample(samples, sample_rate, self.sample_rate, quality="HQ")
        return samples

    def _decode_serialized(
        self, samples: np.ndarray[Any, np.dtype[np.float32]]
    ) -> str:
        # A timed-out asyncio waiter cannot stop native ONNX immediately. Keep
        # serialization owned by the worker until native decode actually exits.
        with self._decode_lock:
            return self._decode(samples)

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
