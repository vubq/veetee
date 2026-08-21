"""Silero VAD runtime, ONNX engine wrapper, injected test engine, and stream adapter."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

from .contract import (
    SileroVADConfig,
    VADAdmissionTimeoutError,
    VADEndReason,
    VADEngineProtocol,
    VadEvent,
    VadEventKind,
    VADModelError,
    VADNotReadyError,
    VADUtterance,
)

logger = logging.getLogger("veetee.vad")


class SileroOnnxEngine:
    """Wrapper around ONNX Runtime for Silero VAD model inference."""

    def __init__(self, model_path: str) -> None:
        if not model_path or not model_path.strip():
            raise VADModelError("vad_model_path must be specified for silero_onnx provider")
        path = os.path.abspath(model_path.strip())
        if not os.path.exists(path):
            raise VADModelError(f"VAD model file not found at path: {path}")

        try:
            import numpy as np
            import onnxruntime as ort  # type: ignore[import-untyped]
        except ImportError as exc:
            raise VADModelError(
                f"Required dependencies numpy and onnxruntime are missing: {exc}"
            ) from exc

        self._np = np
        self._ort = ort
        self._model_path = path

        try:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self._session = ort.InferenceSession(self._model_path, opts)
        except Exception as exc:
            raise VADModelError(f"Failed to load ONNX model '{self._model_path}': {exc}") from exc

        input_names = {inp.name for inp in self._session.get_inputs()}
        output_names = {out.name for out in self._session.get_outputs()}
        if input_names != {"input", "state", "sr"} or output_names != {
            "output",
            "stateN",
        }:
            raise VADModelError(
                "Unsupported Silero ONNX contract; expected v5 input/state/sr model"
            )

    @property
    def is_ready(self) -> bool:
        return hasattr(self, "_session") and self._session is not None

    def initial_state(self) -> Any:
        np = self._np
        recurrent = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, 64), dtype=np.float32)
        return recurrent, context

    def run_inference(self, pcm_512: bytes, state: Any) -> tuple[float, Any]:
        if not self.is_ready:
            raise VADNotReadyError("ONNX session is not ready")

        np = self._np
        # Convert s16le PCM to float32 normalized [-1.0, 1.0]
        pcm_arr = np.frombuffer(pcm_512, dtype=np.int16).astype(np.float32) / 32768.0
        audio_window = np.expand_dims(pcm_arr, axis=0)  # shape (1, 512)
        sr_tensor = np.array(16000, dtype=np.int64)
        recurrent, context = state
        audio_tensor = np.concatenate((context, audio_window), axis=1)
        inputs = {
            "input": audio_tensor,
            "sr": sr_tensor,
            "state": recurrent,
        }
        outputs = self._session.run(None, inputs)
        prob = float(outputs[0].item())
        new_recurrent = outputs[1]
        new_context = audio_tensor[:, -64:]
        return prob, (new_recurrent, new_context)

    def close(self) -> None:
        self._session = None


class InjectedVADEngine:
    """Deterministic mock VAD engine for unit testing without model files or ONNX."""

    def __init__(
        self,
        handler: Callable[[bytes, Any], tuple[float, Any]] | list[float] | None = None,
    ) -> None:
        self._handler = handler
        self._closed = False

    @property
    def is_ready(self) -> bool:
        return not self._closed

    def initial_state(self) -> Any:
        return 0

    def run_inference(self, pcm_512: bytes, state: Any) -> tuple[float, Any]:
        if self._closed:
            raise VADNotReadyError("Engine closed")

        if callable(self._handler):
            return self._handler(pcm_512, state)

        if isinstance(self._handler, list):
            step = int(state) if isinstance(state, (int, float)) else 0
            if step < len(self._handler):
                prob = self._handler[step]
            else:
                prob = self._handler[-1] if self._handler else 0.0
            return prob, step + 1

        # Default fallback: compute simple RMS normalized to [0, 1]
        import math
        import struct

        if not pcm_512:
            return 0.0, state + 1 if isinstance(state, int) else 0
        samples = struct.unpack(f"<{len(pcm_512) // 2}h", pcm_512)
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        prob = min(1.0, rms / 1000.0)
        return prob, state + 1 if isinstance(state, int) else 0

    def close(self) -> None:
        self._closed = True


class SileroVADRuntime:
    """Application-scoped Silero VAD runtime managing model loading, warming, and concurrency."""

    def __init__(
        self,
        config: SileroVADConfig,
        engine: VADEngineProtocol | None = None,
        model_path: str = "",
    ) -> None:
        self.config = config
        self._model_path = model_path
        self._engine = engine
        self._ready = False
        self._semaphore: asyncio.Semaphore | None = None
        self._lock = asyncio.Lock()
        self._workers: set[asyncio.Future[tuple[float, Any]]] = set()

    @property
    def is_ready(self) -> bool:
        return self._ready and (self._engine is not None and self._engine.is_ready)

    async def startup(self) -> None:
        async with self._lock:
            if self._ready:
                return

            self._semaphore = asyncio.Semaphore(self.config.max_concurrency)

            if self._engine is None:
                self._engine = SileroOnnxEngine(self._model_path)

            if not self._engine.is_ready:
                raise VADNotReadyError("VAD engine failed to initialize")

            # Warming run
            dummy_pcm = b"\x00" * self.config.window_bytes
            initial_st = self._engine.initial_state()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._engine.run_inference, dummy_pcm, initial_st)

            self._ready = True
            logger.info("VAD runtime initialized and warmed up successfully")

    async def shutdown(self) -> None:
        async with self._lock:
            self._ready = False
            if self._workers:
                await asyncio.gather(*tuple(self._workers), return_exceptions=True)
            if self._engine is not None:
                self._engine.close()

    async def run_inference(self, pcm_512: bytes, state: Any) -> tuple[float, Any]:
        if not self.is_ready or self._semaphore is None or self._engine is None:
            raise VADNotReadyError("VAD runtime is not ready")
        semaphore = self._semaphore
        engine = self._engine
        if len(pcm_512) != self.config.window_bytes:
            raise ValueError(
                f"VAD inference requires exactly {self.config.window_bytes} PCM bytes"
            )

        # Admission control with timeout
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=self.config.admission_timeout_seconds,
            )
        except TimeoutError:
            raise VADAdmissionTimeoutError(
                f"VAD concurrency limit ({self.config.max_concurrency}) reached; "
                f"admission timed out after {self.config.admission_timeout_seconds}s"
            ) from None

        loop = asyncio.get_running_loop()
        try:
            async with self._lock:
                if not self.is_ready:
                    raise VADNotReadyError("VAD runtime is shutting down")
                worker = loop.run_in_executor(None, engine.run_inference, pcm_512, state)
                # Register under the lifecycle lock so shutdown cannot miss a worker.
                self._workers.add(worker)
        except BaseException:
            semaphore.release()
            raise

        # Cancellation cannot stop native ONNX work. Keep the permit until the
        # worker actually exits so max_concurrency remains a bound on native calls.

        def release_worker(future: asyncio.Future[tuple[float, Any]]) -> None:
            self._workers.discard(future)
            semaphore.release()

        worker.add_done_callback(release_worker)
        return await asyncio.shield(worker)

    def create_stream(self) -> SileroVADStream:
        if not self.is_ready:
            raise VADNotReadyError("Cannot create stream when VAD runtime is not ready")
        initial_st = self._engine.initial_state() if self._engine else None
        return SileroVADStream(self.config, self, initial_st)


class SileroVADStream:
    """Per-turn isolated VAD stream handling PCM rechunking and boundary state machine."""

    def __init__(
        self,
        config: SileroVADConfig,
        runtime: SileroVADRuntime,
        initial_state: Any,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._initial_state = initial_state

        self._pcm_buffer = bytearray()
        self._pre_roll_buffer = bytearray()
        self._utterance_pcm = bytearray()

        self._recurrent_state = initial_state
        self._processed_samples = 0
        self._state = "idle"  # idle, speaking, ended
        self._speech_start_sample: int | None = None
        self._detected_speech_start_sample: int | None = None
        self._speech_start_ms: float | None = None
        self._silence_samples_count = 0
        self._utterance_result: VADUtterance | None = None

        self._cancelled = False
        self._generation = 0

    def reset(self) -> None:
        """Resets stream state for a fresh turn."""
        self._generation += 1
        self._pcm_buffer.clear()
        self._pre_roll_buffer.clear()
        self._utterance_pcm.clear()
        self._recurrent_state = self._initial_state
        self._processed_samples = 0
        self._state = "idle"
        self._speech_start_sample = None
        self._detected_speech_start_sample = None
        self._speech_start_ms = None
        self._silence_samples_count = 0
        self._utterance_result = None
        self._cancelled = False

    def cancel(self) -> None:
        """Cancels stream and prevents further events/results."""
        self._cancelled = True
        self._generation += 1

    async def process_pcm_async(self, pcm_bytes: bytes) -> list[VadEvent]:
        if self._cancelled or self._state == "ended":
            return []

        if not pcm_bytes:
            return []
        if len(pcm_bytes) % (self._config.sample_width * self._config.channels) != 0:
            raise ValueError("PCM input must contain complete s16le mono samples")

        self._pcm_buffer.extend(pcm_bytes)
        window_bytes = self._config.window_bytes
        events: list[VadEvent] = []

        while len(self._pcm_buffer) >= window_bytes:
            if self._cancelled or self._state == "ended":
                break

            window_pcm = bytes(self._pcm_buffer[:window_bytes])
            del self._pcm_buffer[:window_bytes]

            w_start_sample = self._processed_samples
            w_end_sample = w_start_sample + self._config.window_samples
            self._processed_samples += self._config.window_samples

            current_gen = self._generation
            prob, new_state = await self._runtime.run_inference(
                window_pcm, self._recurrent_state
            )

            # Stale result check after async inference call
            if self._cancelled or self._generation != current_gen:
                return []

            self._recurrent_state = new_state

            w_events = self._update_boundary(
                window_pcm, w_start_sample, w_end_sample, prob
            )
            events.extend(w_events)

            # Retain only audio preceding the next detected speech start.
            pre_roll_max_bytes = self._config.pre_roll_samples * self._config.sample_width
            if self._state == "idle" and pre_roll_max_bytes > 0:
                self._pre_roll_buffer.extend(window_pcm)
                if len(self._pre_roll_buffer) > pre_roll_max_bytes:
                    del self._pre_roll_buffer[:-pre_roll_max_bytes]

        return events

    def _update_boundary(
        self, window_pcm: bytes, w_start_sample: int, w_end_sample: int, prob: float
    ) -> list[VadEvent]:
        events: list[VadEvent] = []

        if self._state == "idle":
            if prob >= self._config.threshold:
                self._state = "speaking"
                pre_roll_samples = min(w_start_sample, self._config.pre_roll_samples)
                self._speech_start_sample = w_start_sample - pre_roll_samples
                self._detected_speech_start_sample = w_start_sample
                self._speech_start_ms = (
                    self._speech_start_sample / self._config.sample_rate
                ) * 1000.0

                self._utterance_pcm = bytearray()
                pre_roll_bytes = pre_roll_samples * self._config.sample_width
                if pre_roll_bytes > 0 and len(self._pre_roll_buffer) >= pre_roll_bytes:
                    self._utterance_pcm.extend(self._pre_roll_buffer[-pre_roll_bytes:])
                self._utterance_pcm.extend(window_pcm)

                self._silence_samples_count = 0
                frame_idx = w_start_sample // self._config.window_samples
                events.append(VadEvent(kind=VadEventKind.SPEECH_START, frame_index=frame_idx))
            else:
                frame_idx = w_start_sample // self._config.window_samples
                events.append(VadEvent(kind=VadEventKind.SILENCE, frame_index=frame_idx))

        elif self._state == "speaking":
            self._utterance_pcm.extend(window_pcm)

            if prob >= self._config.neg_threshold:
                self._silence_samples_count = 0
            else:
                self._silence_samples_count += self._config.window_samples

            assert self._speech_start_sample is not None
            assert self._detected_speech_start_sample is not None
            assert self._speech_start_ms is not None
            current_duration_samples = w_end_sample - self._detected_speech_start_sample

            # Silence end threshold reached
            if self._silence_samples_count >= self._config.end_silence_samples:
                speech_duration = current_duration_samples - self._silence_samples_count
                if speech_duration < self._config.min_speech_samples:
                    # Noise / short burst rejection
                    self._state = "idle"
                    self._speech_start_sample = None
                    self._detected_speech_start_sample = None
                    self._speech_start_ms = None
                    self._utterance_pcm.clear()
                    self._silence_samples_count = 0
                    frame_idx = w_start_sample // self._config.window_samples
                    events.append(VadEvent(kind=VadEventKind.SILENCE, frame_index=frame_idx))
                else:
                    # Valid speech segment ended
                    self._state = "ended"
                    speech_end_sample = w_end_sample - self._silence_samples_count
                    speech_end_ms = (
                        speech_end_sample / self._config.sample_rate
                    ) * 1000.0

                    trailing_silence_bytes = (
                        self._silence_samples_count * self._config.sample_width
                    )
                    if trailing_silence_bytes > 0 and trailing_silence_bytes <= len(
                        self._utterance_pcm
                    ):
                        final_pcm = bytes(
                            self._utterance_pcm[:-trailing_silence_bytes]
                        )
                    else:
                        final_pcm = bytes(self._utterance_pcm)

                    self._utterance_result = VADUtterance(
                        start_sample=self._speech_start_sample,
                        end_sample=speech_end_sample,
                        start_ms=self._speech_start_ms,
                        end_ms=speech_end_ms,
                        pcm_data=final_pcm,
                        end_reason=VADEndReason.END_SILENCE,
                        sample_rate=self._config.sample_rate,
                        sample_width=self._config.sample_width,
                        channels=self._config.channels,
                    )
                    frame_idx = w_start_sample // self._config.window_samples
                    events.append(VadEvent(kind=VadEventKind.SPEECH_END, frame_index=frame_idx))

            # Max utterance threshold reached
            elif current_duration_samples >= self._config.max_utterance_samples:
                self._state = "ended"
                speech_end_sample = w_end_sample
                speech_end_ms = (speech_end_sample / self._config.sample_rate) * 1000.0
                self._utterance_result = VADUtterance(
                    start_sample=self._speech_start_sample,
                    end_sample=speech_end_sample,
                    start_ms=self._speech_start_ms,
                    end_ms=speech_end_ms,
                    pcm_data=bytes(self._utterance_pcm),
                    end_reason=VADEndReason.MAX_UTTERANCE,
                    sample_rate=self._config.sample_rate,
                    sample_width=self._config.sample_width,
                    channels=self._config.channels,
                )
                frame_idx = w_start_sample // self._config.window_samples
                events.append(VadEvent(kind=VadEventKind.SPEECH_END, frame_index=frame_idx))
            else:
                frame_idx = w_start_sample // self._config.window_samples
                events.append(VadEvent(kind=VadEventKind.PROCESSING, frame_index=frame_idx))

        return events

    def finish(self) -> VADUtterance | None:
        """Finalizes the turn and returns the VAD utterance if speech was detected."""
        if self._cancelled:
            return None

        if self._utterance_result is not None:
            return self._utterance_result

        if self._state == "speaking" and self._speech_start_sample is not None:
            assert self._detected_speech_start_sample is not None
            assert self._speech_start_ms is not None
            duration_samples = self._processed_samples - self._detected_speech_start_sample
            if duration_samples >= self._config.min_speech_samples:
                speech_end_sample = self._processed_samples
                speech_end_ms = (
                    speech_end_sample / self._config.sample_rate
                ) * 1000.0
                self._state = "ended"
                self._utterance_result = VADUtterance(
                    start_sample=self._speech_start_sample,
                    end_sample=speech_end_sample,
                    start_ms=self._speech_start_ms,
                    end_ms=speech_end_ms,
                    pcm_data=bytes(self._utterance_pcm),
                    end_reason=VADEndReason.STREAM_END,
                    sample_rate=self._config.sample_rate,
                    sample_width=self._config.sample_width,
                    channels=self._config.channels,
                )
                return self._utterance_result

        return None
