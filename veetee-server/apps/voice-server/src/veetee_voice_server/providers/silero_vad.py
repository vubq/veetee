from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 512
CONTEXT_SAMPLES = 64
CHUNK_MS = CHUNK_SAMPLES * 1000 // SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class VadFrameResult:
    probability: float
    speech_started: bool
    speech_ended: bool
    is_speech: bool


class VadModel(Protocol):
    def predict(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        state: np.ndarray[Any, np.dtype[np.float32]],
        context: np.ndarray[Any, np.dtype[np.float32]],
    ) -> tuple[float, np.ndarray[Any, Any], np.ndarray[Any, Any]]: ...


class SileroVadModel:
    def __init__(self, model_path: Path, *, num_threads: int = 1) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(model_path)
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = num_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self._lock = threading.Lock()

    def predict(
        self,
        samples: np.ndarray[Any, np.dtype[np.float32]],
        state: np.ndarray[Any, np.dtype[np.float32]],
        context: np.ndarray[Any, np.dtype[np.float32]],
    ) -> tuple[float, np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        audio = np.concatenate((context, samples.reshape(1, -1)), axis=1).astype(np.float32)
        with self._lock:
            probability, next_state = self._session.run(
                None,
                {
                    "input": audio,
                    "state": state,
                    "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                },
            )
        return float(probability.item()), next_state, audio[:, -CONTEXT_SAMPLES:]


class SileroVadSession:
    def __init__(
        self,
        model: VadModel,
        *,
        threshold: float = 0.5,
        release_threshold: float = 0.35,
        min_silence_ms: int = 400,
        max_speech_seconds: float = 20.0,
    ) -> None:
        self._model = model
        self._threshold = threshold
        self._release_threshold = release_threshold
        self._min_silence_ms = min_silence_ms
        self._max_speech_ms = (
            int(max_speech_seconds * 1000) if max_speech_seconds > 0 else None
        )
        self._pending = bytearray()
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._speech_active = False
        self._last_speech = False
        self._silence_ms = 0
        self._speech_ms = 0

    def process(self, pcm_s16le: bytes) -> list[VadFrameResult]:
        if len(pcm_s16le) % 2:
            raise ValueError("VAD input must be PCM signed 16-bit little-endian")
        self._pending.extend(pcm_s16le)
        chunk_bytes = CHUNK_SAMPLES * 2
        results: list[VadFrameResult] = []
        while len(self._pending) >= chunk_bytes:
            chunk = bytes(self._pending[:chunk_bytes])
            del self._pending[:chunk_bytes]
            samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            probability, self._state, self._context = self._model.predict(
                samples, self._state, self._context
            )
            is_speech = probability >= self._threshold or (
                self._last_speech and probability > self._release_threshold
            )
            self._last_speech = is_speech
            started = False
            ended = False
            if is_speech:
                self._silence_ms = 0
                if not self._speech_active:
                    self._speech_active = True
                    started = True
            elif self._speech_active:
                self._silence_ms += CHUNK_MS
                ended = self._silence_ms >= self._min_silence_ms

            if self._speech_active:
                self._speech_ms += CHUNK_MS
                ended = ended or (
                    self._max_speech_ms is not None
                    and self._speech_ms >= self._max_speech_ms
                )
            if ended:
                self._speech_active = False
                self._speech_ms = 0
                self._silence_ms = 0

            results.append(VadFrameResult(probability, started, ended, is_speech))
        return results

    def reset(self) -> None:
        self._pending.clear()
        self._state.fill(0)
        self._context.fill(0)
        self._speech_active = False
        self._last_speech = False
        self._silence_ms = 0
        self._speech_ms = 0
