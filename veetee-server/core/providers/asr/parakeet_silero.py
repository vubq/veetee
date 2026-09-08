import asyncio
import logging
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np
import onnxruntime as ort

from core.providers.asr.base import BaseASR
from core.turn_metrics import TurnMetricsRecorder


logger = logging.getLogger("ParakeetSileroASR")


@dataclass
class _QueuedUtterance:
    generation: int
    pcm: np.ndarray
    enqueued_perf: float
    audio_ms: float


class _ParakeetRuntime:
    """One shared Parakeet model for all websocket sessions."""

    _model = None
    _model_name: Optional[str] = None
    _device: Optional[str] = None
    _load_lock: Optional[asyncio.Lock] = None
    _infer_lock: Optional[asyncio.Lock] = None

    @staticmethod
    def _find_cached_checkpoint(model_name: str) -> Optional[Path]:
        checkpoint_name = model_name.rsplit("/", 1)[-1] + ".nemo"
        nemo_cache = Path.home() / ".cache" / "torch" / "NeMo"
        if not nemo_cache.exists():
            return None

        matches = list(nemo_cache.glob(f"**/{checkpoint_name}"))
        if not matches:
            return None
        return max(matches, key=lambda path: path.stat().st_mtime)

    @classmethod
    def _locks(cls) -> tuple[asyncio.Lock, asyncio.Lock]:
        if cls._load_lock is None:
            cls._load_lock = asyncio.Lock()
        if cls._infer_lock is None:
            cls._infer_lock = asyncio.Lock()
        return cls._load_lock, cls._infer_lock

    @classmethod
    async def ensure_loaded(cls, model_name: str, device: str) -> None:
        load_lock, _ = cls._locks()
        async with load_lock:
            if cls._model is not None and cls._model_name == model_name:
                return

            loop = asyncio.get_running_loop()
            model, resolved_device = await loop.run_in_executor(
                None,
                cls._load_model,
                model_name,
                device,
            )
            cls._model = model
            cls._model_name = model_name
            cls._device = resolved_device

    @staticmethod
    def _load_model(model_name: str, requested_device: str):
        import torch
        import nemo.collections.asr as nemo_asr
        from copy import deepcopy

        use_cuda = requested_device.lower().startswith("cuda") and torch.cuda.is_available()
        resolved_device = "cuda" if use_cuda else "cpu"
        logger.info("Loading Parakeet model %s on %s...", model_name, resolved_device)

        # Restore on CPU first to avoid a large temporary GPU allocation. The
        # GTX 1650 Ti only has 4 GB and VieNeu is already resident on the GPU.
        cached_checkpoint = _ParakeetRuntime._find_cached_checkpoint(model_name)
        if cached_checkpoint is not None:
            logger.info("Restoring cached Parakeet checkpoint: %s", cached_checkpoint)
            model = nemo_asr.models.ASRModel.restore_from(
                str(cached_checkpoint),
                map_location=torch.device("cpu"),
            )
        else:
            model = nemo_asr.models.ASRModel.from_pretrained(
                model_name,
                map_location=torch.device("cpu"),
            )
        model.eval()

        # Preserve the model's normal greedy decoder, but expose calibrated
        # per-word acoustic confidence so the application can spend an LLM
        # correction call only on genuinely uncertain utterances.
        decoding_cfg = deepcopy(model.cfg.decoding)
        decoding_cfg.confidence_cfg.preserve_frame_confidence = True
        decoding_cfg.confidence_cfg.preserve_token_confidence = True
        decoding_cfg.confidence_cfg.preserve_word_confidence = True
        decoding_cfg.confidence_cfg.exclude_blank = True
        decoding_cfg.confidence_cfg.aggregation = "min"
        decoding_cfg.confidence_cfg.method_cfg.name = "max_prob"
        model.change_decoding_strategy(decoding_cfg, verbose=False)

        if use_cuda:
            # Half the acoustic model before moving it to CUDA. This keeps the
            # persistent Parakeet allocation small enough to coexist with TTS.
            model = model.half().to(torch.device("cuda"))
        else:
            model = model.float().to(torch.device("cpu"))

        logger.info("Parakeet model ready on %s", resolved_device)
        return model, resolved_device

    @classmethod
    async def transcribe(cls, pcm_float32: np.ndarray) -> tuple[str, Optional[float]]:
        text, confidence, _ = await cls.transcribe_if_current(
            pcm_float32,
            is_current=lambda: True,
        )
        return text, confidence

    @classmethod
    async def transcribe_if_current(
        cls,
        pcm_float32: np.ndarray,
        *,
        is_current: Callable[[], bool],
        on_lock_acquired: Optional[Callable[[float], None]] = None,
    ) -> tuple[str, Optional[float], bool]:
        if cls._model is None:
            raise RuntimeError("Parakeet model has not been loaded")

        _, infer_lock = cls._locks()
        wait_started = time.perf_counter()
        await infer_lock.acquire()
        try:
            if on_lock_acquired is not None:
                on_lock_acquired((time.perf_counter() - wait_started) * 1000.0)
            if not is_current():
                return "", None, True
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, cls._transcribe_sync, pcm_float32)
            return result[0], result[1], False
        finally:
            infer_lock.release()

    @classmethod
    def _transcribe_sync(cls, pcm_float32: np.ndarray) -> str:
        import torch

        model = cls._model
        if model is None:
            return ""

        with torch.inference_mode():
            if cls._device == "cuda":
                # NeMo's transcription path creates float32 input features. The
                # model itself stays FP16; autocast handles mixed input safely.
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    output = model.transcribe(
                        pcm_float32,
                        batch_size=1,
                        return_hypotheses=True,
                        num_workers=0,
                        verbose=False,
                    )
            else:
                output = model.transcribe(
                    pcm_float32,
                    batch_size=1,
                    return_hypotheses=True,
                    num_workers=0,
                    verbose=False,
                )

        if isinstance(output, tuple):
            output = output[0]
        if isinstance(output, list):
            value = output[0] if output else ""
        else:
            value = output
        text = getattr(value, "text", value)
        word_confidence = getattr(value, "word_confidence", None) or []
        confidence = min(float(score) for score in word_confidence) if word_confidence else None
        return str(text or "").strip(), confidence


class ParakeetSileroASR(BaseASR):
    """Local Silero endpointing followed by Parakeet Vietnamese CTC ASR."""

    FRAME_SAMPLES = 512
    FRAME_BYTES = FRAME_SAMPLES * 2
    FRAME_MS = FRAME_SAMPLES * 1000 / 16000

    @classmethod
    async def preload(cls, model: str, device: str) -> None:
        """Warm the shared Parakeet runtime before accepting microphone clients."""
        await _ParakeetRuntime.ensure_loaded(model, device)

    def __init__(
        self,
        model: str = "nvidia/parakeet-ctc-0.6b-vi",
        sample_rate: int = 16000,
        device: str = "cuda",
        vad_model_path: str = "models/silero-vad/silero_vad.onnx",
        vad_threshold: float = 0.5,
        vad_threshold_low: float = 0.3,
        min_silence_duration_ms: int = 450,
        min_speech_duration_ms: int = 160,
        speech_start_frames: int = 2,
        pre_speech_pad_ms: int = 512,
        utterance_queue_max: int = 2,
        max_utterance_ms: int = 30000,
        metrics_recorder: Optional[TurnMetricsRecorder] = None,
        on_transcript_callback: Optional[
            Callable[..., Awaitable[None]]
        ] = None,
        on_speech_started_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ):
        if sample_rate != 16000:
            raise ValueError("Silero VAD provider currently requires PCM16 16 kHz input")

        self.model_name = model
        self.sample_rate = sample_rate
        self.device = device
        self.vad_model_path = Path(vad_model_path)
        self.vad_threshold = float(vad_threshold)
        self.vad_threshold_low = float(vad_threshold_low)
        self.min_silence_duration_ms = max(int(min_silence_duration_ms), 160)
        self.min_speech_duration_ms = max(int(min_speech_duration_ms), 64)
        self.speech_start_frames = max(int(speech_start_frames), 1)
        self.pre_speech_pad_ms = max(int(pre_speech_pad_ms), 0)
        self.utterance_queue_max = max(1, int(utterance_queue_max))
        self.max_utterance_ms = max(self.min_speech_duration_ms, int(max_utterance_ms))
        self.metrics_recorder = metrics_recorder
        self.on_transcript_callback = on_transcript_callback
        self.on_speech_started_callback = on_speech_started_callback

        pre_roll_frames = max(1, int(round(self.pre_speech_pad_ms / self.FRAME_MS)))
        self._pre_roll: deque[bytes] = deque(maxlen=pre_roll_frames)
        self._pcm_pending = bytearray()
        self._speech_buffer = bytearray()
        self._speech_active = False
        self._candidate_voice_frames = 0
        self._voiced_ms = 0.0
        self._silence_ms = 0.0
        self._last_is_voice = False

        self._vad_session: Optional[ort.InferenceSession] = None
        self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
        self._vad_context = np.zeros((1, 64), dtype=np.float32)

        self._utterance_queue: asyncio.Queue = asyncio.Queue(maxsize=self.utterance_queue_max)
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._start_error: Optional[str] = None
        self.last_word_confidence: Optional[float] = None
        self._capture_generation = 0
        self._utterance_generation = 0

    async def start(self):
        if self._running:
            return
        if self._start_error is not None:
            raise RuntimeError(f"Parakeet ASR initialization previously failed: {self._start_error}")

        try:
            model_path = self.vad_model_path
            if not model_path.is_absolute():
                model_path = Path(__file__).resolve().parents[3] / model_path
            if not model_path.exists():
                raise FileNotFoundError(f"Silero VAD model not found: {model_path}")

            options = ort.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
            self._vad_session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
                sess_options=options,
            )

            await _ParakeetRuntime.ensure_loaded(self.model_name, self.device)
            self._running = True
            self._worker_task = asyncio.create_task(self._transcription_worker())
            logger.info(
                "Local ASR ready: Silero VAD -> %s (silence=%d ms)",
                self.model_name,
                self.min_silence_duration_ms,
            )
        except Exception as exc:
            self._vad_session = None
            self._start_error = str(exc)
            raise

    async def send_audio(self, pcm_bytes: bytes, capture_generation: Optional[int] = None):
        if not pcm_bytes:
            return
        if self._start_error is not None:
            return
        if not self._running:
            await self.start()

        if capture_generation is not None:
            self._capture_generation = int(capture_generation)

        self._pcm_pending.extend(pcm_bytes)
        while len(self._pcm_pending) >= self.FRAME_BYTES:
            frame = bytes(self._pcm_pending[: self.FRAME_BYTES])
            del self._pcm_pending[: self.FRAME_BYTES]
            await self._process_frame(frame)

    def _speech_probability(self, frame: bytes) -> float:
        if self._vad_session is None:
            return 0.0

        audio_int16 = np.frombuffer(frame, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        audio_input = np.concatenate(
            [self._vad_context, audio_float32.reshape(1, -1)],
            axis=1,
        ).astype(np.float32)
        outputs = self._vad_session.run(
            None,
            {
                "input": audio_input,
                "state": self._vad_state,
                "sr": np.array(16000, dtype=np.int64),
            },
        )
        self._vad_state = outputs[1]
        self._vad_context = audio_input[:, -64:]
        return float(outputs[0].item())

    async def _process_frame(self, frame: bytes):
        probability = self._speech_probability(frame)
        if probability >= self.vad_threshold:
            is_voice = True
        elif probability <= self.vad_threshold_low:
            is_voice = False
        else:
            is_voice = self._last_is_voice
        self._last_is_voice = is_voice

        if not self._speech_active:
            self._pre_roll.append(frame)
            if is_voice:
                self._candidate_voice_frames += 1
            else:
                self._candidate_voice_frames = 0

            if self._candidate_voice_frames >= self.speech_start_frames:
                self._speech_active = True
                self._utterance_generation = self._capture_generation
                self._speech_buffer = bytearray().join(self._pre_roll)
                self._pre_roll.clear()
                self._voiced_ms = self._candidate_voice_frames * self.FRAME_MS
                self._silence_ms = 0.0
                logger.debug("Silero VAD speech started (p=%.3f)", probability)
                if self.on_speech_started_callback:
                    await self.on_speech_started_callback(self._utterance_generation)
            return

        self._speech_buffer.extend(frame)
        if is_voice:
            self._voiced_ms += self.FRAME_MS
            self._silence_ms = 0.0
        else:
            self._silence_ms += self.FRAME_MS

        if self._silence_ms >= self.min_silence_duration_ms:
            await self._finish_utterance(endpoint_reason="silence")
        elif len(self._speech_buffer) * 1000 / (self.sample_rate * 2) >= self.max_utterance_ms:
            logger.warning("Finalizing ASR utterance at max duration=%dms", self.max_utterance_ms)
            await self._finish_utterance(endpoint_reason="max_duration")

    async def _finish_utterance(self, endpoint_reason: str = "unknown"):
        if not self._speech_active:
            return

        audio_bytes = bytes(self._speech_buffer)
        voiced_ms = self._voiced_ms
        trailing_silence_ms = self._silence_ms
        utterance_generation = self._utterance_generation
        self._reset_utterance_state()

        logger.info(
            "Silero utterance endpoint: reason=%s trailing_silence=%.0fms voiced=%.0fms generation=%s",
            endpoint_reason,
            trailing_silence_ms,
            voiced_ms,
            utterance_generation,
        )
        endpoint_perf = time.perf_counter()
        if self.metrics_recorder is not None:
            self.metrics_recorder.record_capture_event(
                utterance_generation,
                "speech_endpoint",
                event_perf=endpoint_perf,
                reason=endpoint_reason,
                trailing_silence_ms=round(trailing_silence_ms, 3),
                voiced_ms=round(voiced_ms, 3),
            )
            if trailing_silence_ms > 0:
                self.metrics_recorder.record_capture_event(
                    utterance_generation,
                    "last_voiced_sample_estimate",
                    event_perf=endpoint_perf - trailing_silence_ms / 1000.0,
                    frame_error_ms=self.FRAME_MS,
                )

        if voiced_ms < self.min_speech_duration_ms:
            logger.debug("Silero rejected short speech candidate (%.0f ms)", voiced_ms)
            if self.on_transcript_callback:
                await self.on_transcript_callback("", True, True, utterance_generation)
            return

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size:
            # Remove a small DC offset and normalize quiet browser microphones.
            # iOS/WebKit can deliver valid speech at a much lower level than the
            # audio used to train/test Parakeet. Silero still detects it, but the
            # acoustic model may otherwise collapse to the unknown token.
            pcm = pcm - float(np.mean(pcm))
            rms = float(np.sqrt(np.mean(np.square(pcm), dtype=np.float64)))
            peak = float(np.max(np.abs(pcm)))
            gain = 1.0
            if rms > 1e-5 and rms < 0.075:
                gain = min(0.09 / rms, 8.0)
                if peak > 1e-5:
                    gain = min(gain, 0.95 / peak)
                pcm = np.clip(pcm * gain, -0.98, 0.98).astype(np.float32, copy=False)
            logger.info(
                "ASR utterance audio: %.0f ms, %d samples, rms=%.4f, peak=%.4f, gain=%.2fx",
                pcm.size * 1000 / self.sample_rate,
                pcm.size,
                rms,
                peak,
                gain,
            )
        await self._enqueue_utterance(utterance_generation, pcm)

    async def _enqueue_utterance(self, generation: int, pcm: np.ndarray) -> None:
        audio_ms = pcm.size * 1000.0 / self.sample_rate
        if self._utterance_queue.full():
            kept = []
            while True:
                try:
                    item = self._utterance_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._utterance_queue.task_done()
                if item is None or item.generation == self._capture_generation:
                    kept.append(item)
                else:
                    logger.info(
                        "Dropped stale ASR job before inference generation=%s current=%s",
                        item.generation,
                        self._capture_generation,
                    )
                    if self.metrics_recorder is not None:
                        self.metrics_recorder.record_capture_event(
                            item.generation,
                            "asr_drop_stale_queue",
                            age_ms=round((time.perf_counter() - item.enqueued_perf) * 1000.0, 3),
                        )
            for item in kept:
                self._utterance_queue.put_nowait(item)

        if self._utterance_queue.full():
            logger.warning(
                "ASR queue full; rejecting utterance generation=%s audio_ms=%.0f",
                generation,
                audio_ms,
            )
            if self.metrics_recorder is not None:
                self.metrics_recorder.record_capture_event(
                    generation,
                    "asr_queue_rejected",
                    audio_ms=round(audio_ms, 3),
                )
            if self.on_transcript_callback:
                await self.on_transcript_callback("", True, True, generation)
            return

        item = _QueuedUtterance(
            generation=generation,
            pcm=pcm,
            enqueued_perf=time.perf_counter(),
            audio_ms=audio_ms,
        )
        self._utterance_queue.put_nowait(item)
        if self.metrics_recorder is not None:
            self.metrics_recorder.record_capture_event(
                generation,
                "asr_enqueue",
                audio_ms=round(audio_ms, 3),
                queue_size=self._utterance_queue.qsize(),
            )

    def _reset_utterance_state(self):
        self._speech_buffer.clear()
        self._pre_roll.clear()
        self._speech_active = False
        self._candidate_voice_frames = 0
        self._voiced_ms = 0.0
        self._silence_ms = 0.0
        self._last_is_voice = False

    async def _transcription_worker(self):
        while True:
            queued = await self._utterance_queue.get()
            try:
                if queued is None:
                    return
                utterance_generation = queued.generation
                pcm = queued.pcm
                if utterance_generation != self._capture_generation:
                    logger.info(
                        "Dropped stale ASR job before lock generation=%s current=%s",
                        utterance_generation,
                        self._capture_generation,
                    )
                    if self.metrics_recorder is not None:
                        self.metrics_recorder.record_capture_event(
                            utterance_generation,
                            "asr_drop_stale_before_lock",
                            age_ms=round((time.perf_counter() - queued.enqueued_perf) * 1000.0, 3),
                        )
                    continue
                try:
                    def _on_lock_acquired(wait_ms: float):
                        if self.metrics_recorder is not None:
                            self.metrics_recorder.record_capture_event(
                                utterance_generation,
                                "asr_lock_acquired",
                                wait_ms=round(wait_ms, 3),
                                queue_age_ms=round((time.perf_counter() - queued.enqueued_perf) * 1000.0, 3),
                            )

                    infer_started = time.perf_counter()
                    text, confidence, skipped = await _ParakeetRuntime.transcribe_if_current(
                        pcm,
                        is_current=lambda: utterance_generation == self._capture_generation,
                        on_lock_acquired=_on_lock_acquired,
                    )
                    if skipped:
                        logger.info(
                            "Dropped stale ASR job after lock generation=%s current=%s",
                            utterance_generation,
                            self._capture_generation,
                        )
                        if self.metrics_recorder is not None:
                            self.metrics_recorder.record_capture_event(
                                utterance_generation,
                                "asr_drop_stale_after_lock",
                            )
                        continue
                    if self.metrics_recorder is not None:
                        self.metrics_recorder.record_capture_event(
                            utterance_generation,
                            "asr_infer_end",
                            infer_ms=round((time.perf_counter() - infer_started) * 1000.0, 3),
                        )
                    self.last_word_confidence = confidence
                    logger.info(
                        "Parakeet final transcript: %r (min_word_confidence=%s)",
                        text,
                        f"{confidence:.3f}" if confidence is not None else "n/a",
                    )
                    if self.metrics_recorder is not None:
                        self.metrics_recorder.record_capture_event(
                            utterance_generation,
                            "asr_final",
                            text_chars=len(text or ""),
                            min_word_confidence=(
                                round(float(confidence), 4) if confidence is not None else None
                            ),
                        )
                    if text and not any(char.isalnum() for char in text):
                        # Keep only failing utterances, in /tmp, so a real browser
                        # sample can be replayed through Parakeet during debugging.
                        debug_path = Path("/tmp") / f"veetee_asr_unknown_{time.time_ns()}.wav"
                        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16)
                        with wave.open(str(debug_path), "wb") as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(self.sample_rate)
                            wav_file.writeframes(pcm16.tobytes())
                        logger.warning("Saved failed ASR utterance for diagnosis: %s", debug_path)
                except Exception as exc:
                    logger.error("Parakeet transcription failed: %s", exc, exc_info=True)
                    if self.metrics_recorder is not None:
                        self.metrics_recorder.record_capture_event(
                            queued.generation,
                            "asr_infer_failed",
                            error=type(exc).__name__,
                        )
                    text = ""
                    self.last_word_confidence = None
                if self.on_transcript_callback:
                    await self.on_transcript_callback(text, True, True, utterance_generation)
            finally:
                self._utterance_queue.task_done()

    def invalidate_capture(self, capture_generation: int):
        self._capture_generation = int(capture_generation)
        self._utterance_generation = self._capture_generation
        self._pcm_pending.clear()
        self._reset_utterance_state()

    async def finalize(self):
        if not self._running:
            return

        # Preserve a partial final PCM frame when the user explicitly stops the
        # microphone. Silero has already classified the preceding full frames.
        if self._speech_active and self._pcm_pending:
            self._speech_buffer.extend(self._pcm_pending)
        self._pcm_pending.clear()

        if self._speech_active:
            await self._finish_utterance(endpoint_reason="client_finalize")

    async def stop(self):
        if not self._running and self._worker_task is None:
            return
        await self.finalize()
        self._running = False

        if self._worker_task is not None:
            await self._utterance_queue.put(None)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        self._vad_session = None
        self._pcm_pending.clear()
        self._reset_utterance_state()
        self._vad_state.fill(0)
        self._vad_context.fill(0)
        logger.info("Parakeet + Silero ASR session stopped")
