"""PhoWhisper ASR runtime, CTranslate2 engine wrapper, injected test engine, and stream adapter."""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from .contract import (
    ASRAdmissionTimeoutError,
    ASREngineProtocol,
    ASRError,
    ASRModelError,
    ASRNotReadyError,
    ASROversizedAudioError,
    ASRResult,
    ASRSegment,
    ASRTimeoutError,
    ASRTranscribeRequest,
    ASRValidationError,
    PhoWhisperConfig,
    normalize_transcript,
)

logger = logging.getLogger("veetee.asr")


class PhoWhisperCtranslateEngine:
    """Wrapper around faster-whisper (CTranslate2) for PhoWhisper model inference."""

    def __init__(self, config: PhoWhisperConfig) -> None:
        self._config = config
        self._model: Any = None

    def load(self) -> None:
        if self._config.device == "cuda":
            self._preload_cuda_libraries()
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ASRModelError(
                f"Required dependency faster-whisper is missing: {exc}"
            ) from exc

        try:
            self._model = WhisperModel(
                self._config.model_id,
                device=self._config.device,
                compute_type=self._config.compute_type,
                download_root=self._config.download_root,
                local_files_only=self._config.local_files_only,
            )
        except Exception as exc:
            raise ASRModelError(
                f"Failed to load PhoWhisper model '{self._config.model_id}': {exc}"
            ) from exc

    @staticmethod
    def _preload_cuda_libraries() -> None:
        """Load project-local CUDA 12 libraries before CTranslate2 resolves their SONAMEs."""
        libraries = (
            ("nvidia.cublas", "lib/libcublasLt.so.12"),
            ("nvidia.cublas", "lib/libcublas.so.12"),
            ("nvidia.cudnn", "lib/libcudnn.so.9"),
        )
        try:
            for package, relative_path in libraries:
                spec = importlib.util.find_spec(package)
                if spec is None or spec.submodule_search_locations is None:
                    raise ASRModelError(f"Required CUDA runtime package is missing: {package}")
                package_dir = next(iter(spec.submodule_search_locations))
                ctypes.CDLL(
                    f"{package_dir}/{relative_path}",
                    mode=ctypes.RTLD_GLOBAL,
                )
        except (OSError, StopIteration) as exc:
            raise ASRModelError(f"Failed to load CUDA 12 runtime libraries: {exc}") from exc

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def run_inference(
        self, pcm_float32: np.ndarray[Any, np.dtype[np.float32]], language: str
    ) -> tuple[str, list[ASRSegment], dict[str, Any]]:
        if not self.is_ready:
            raise ASRNotReadyError("PhoWhisper engine is not loaded")

        try:
            segments_raw, info = self._model.transcribe(
                pcm_float32,
                language=language or self._config.language,
                beam_size=5,
                vad_filter=False,
            )
            segments: list[ASRSegment] = []
            text_parts: list[str] = []
            for seg in segments_raw:
                text_parts.append(seg.text)
                segments.append(
                    ASRSegment(
                        id=seg.id,
                        seek=seg.seek,
                        start=seg.start,
                        end=seg.end,
                        text=seg.text,
                        tokens=list(seg.tokens) if hasattr(seg, "tokens") else [],
                        temperature=getattr(seg, "temperature", 0.0),
                        avg_logprob=getattr(seg, "avg_logprob", 0.0),
                        compression_ratio=getattr(seg, "compression_ratio", 1.0),
                        no_speech_prob=getattr(seg, "no_speech_prob", 0.0),
                    )
                )
            raw_text = "".join(text_parts)
            metadata = {
                "model_id": self._config.model_id,
                "device": self._config.device,
                "compute_type": self._config.compute_type,
                "language": getattr(info, "language", language),
                "language_probability": getattr(info, "language_probability", 1.0),
                "duration": getattr(info, "duration", 0.0),
            }
            return raw_text, segments, metadata
        except Exception as exc:
            raise ASRError(f"PhoWhisper inference failed: {exc}") from exc

    def close(self) -> None:
        self._model = None


class InjectedASREngine:
    """Mock engine for deterministic testing of PhoWhisperRuntime without CTranslate2."""

    def __init__(
        self,
        transcribe_fn: Callable[
            [np.ndarray[Any, np.dtype[np.float32]], str],
            tuple[str, list[ASRSegment], dict[str, Any]],
        ]
        | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self._transcribe_fn = transcribe_fn
        self._delay_seconds = delay_seconds
        self._ready = True

    @property
    def is_ready(self) -> bool:
        return self._ready

    def run_inference(
        self, pcm_float32: np.ndarray[Any, np.dtype[np.float32]], language: str
    ) -> tuple[str, list[ASRSegment], dict[str, Any]]:
        if not self._ready:
            raise ASRNotReadyError("Injected engine is not ready")
        if self._delay_seconds > 0:
            import time

            time.sleep(self._delay_seconds)
        if self._transcribe_fn:
            return self._transcribe_fn(pcm_float32, language)
        return "Xin chào Veetee", [], {"injected": True}

    def close(self) -> None:
        self._ready = False


class PhoWhisperRuntime:
    """Application-scoped PhoWhisper ASR runtime managing concurrency and warm execution."""

    def __init__(
        self,
        config: PhoWhisperConfig,
        engine: ASREngineProtocol | None = None,
    ) -> None:
        self._config = config
        self._engine: ASREngineProtocol | None = engine
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=config.max_concurrency, thread_name_prefix="phowhisper-asr"
        )
        self._lock = asyncio.Lock()
        self._is_ready = False
        self._is_shutting_down = False
        self._active_tasks: set[asyncio.Task[Any]] = set()

    @property
    def is_ready(self) -> bool:
        return self._is_ready and (self._engine is not None and self._engine.is_ready)

    async def startup(self) -> None:
        async with self._lock:
            if self._is_ready:
                return
            if self._engine is None:
                engine = PhoWhisperCtranslateEngine(self._config)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, engine.load)
                self._engine = engine
            if not self._engine.is_ready:
                raise ASRNotReadyError("PhoWhisper engine failed to initialize")

            # Warmup: run a 0.5s dummy inference
            warmup_pcm = np.zeros(8000, dtype=np.float32)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._executor, self._engine.run_inference, warmup_pcm, self._config.language
            )
            if not self._engine.is_ready:
                raise ASRNotReadyError("PhoWhisper engine became unavailable during warmup")
            self._is_ready = True
            logger.info(
                "phowhisper_runtime_ready",
                extra={
                    "context": {
                        "model_id": self._config.model_id,
                        "device": self._config.device,
                    }
                },
            )

    async def shutdown(self) -> None:
        async with self._lock:
            self._is_shutting_down = True
            self._is_ready = False
            if self._active_tasks:
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
            if self._engine:
                self._engine.close()
                self._engine = None
            self._executor.shutdown(wait=True)
            logger.info("phowhisper_runtime_shutdown")

    def _validate_and_convert_pcm(
        self, request: ASRTranscribeRequest | Any | bytes
    ) -> tuple[np.ndarray[Any, np.dtype[np.float32]], float]:
        pcm_bytes: bytes
        sample_rate = 16000
        sample_width = 2
        channels = 1

        if isinstance(request, bytes):
            pcm_bytes = request
        elif isinstance(request, ASRTranscribeRequest):
            pcm_bytes = request.pcm_data
            sample_rate = request.sample_rate
            sample_width = request.sample_width
            channels = request.channels
        elif hasattr(request, "pcm_data"):
            pcm_bytes = request.pcm_data
            sample_rate = getattr(request, "sample_rate", 16000)
            sample_width = getattr(request, "sample_width", 2)
            channels = getattr(request, "channels", 1)
        else:
            raise ASRValidationError("Unsupported request type for ASR transcription")

        if not pcm_bytes:
            raise ASRValidationError("PCM data cannot be empty")
        if sample_rate != 16000:
            raise ASRValidationError(
                f"Unsupported sample rate: {sample_rate} Hz (expected 16000 Hz)"
            )
        if sample_width != 2:
            raise ASRValidationError(
                f"Unsupported sample width: {sample_width} bytes (expected 2 bytes s16le)"
            )
        if channels != 1:
            raise ASRValidationError(
                f"Unsupported channel count: {channels} (expected 1 mono)"
            )
        if len(pcm_bytes) % 2 != 0:
            raise ASRValidationError(
                "PCM data length must be a multiple of 2 (complete 16-bit samples)"
            )

        num_samples = len(pcm_bytes) // 2
        duration_seconds = num_samples / 16000.0

        if duration_seconds > self._config.max_audio_seconds:
            raise ASROversizedAudioError(
                f"Audio duration {duration_seconds:.2f}s exceeds maximum allowed "
                f"{self._config.max_audio_seconds}s"
            )

        pcm_arr: np.ndarray[Any, np.dtype[np.float32]] = (
            np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        )
        return pcm_arr, duration_seconds

    async def transcribe_async(
        self,
        request: ASRTranscribeRequest | Any | bytes,
        language: str | None = None,
    ) -> ASRResult:
        if not self.is_ready or self._is_shutting_down:
            raise ASRNotReadyError("PhoWhisper runtime is not ready")

        pcm_arr, duration_seconds = self._validate_and_convert_pcm(request)
        request_language = request.language if isinstance(request, ASRTranscribeRequest) else None
        target_lang = language or request_language or self._config.language

        if np.max(np.abs(pcm_arr)) == 0:
            return ASRResult(
                raw_text="",
                normalized_text="",
                language=target_lang,
                duration_seconds=duration_seconds,
                segments=[],
                provider_metadata={"silence": True},
            )

        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._config.total_timeout_seconds
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=min(
                    self._config.admission_timeout_seconds,
                    self._config.total_timeout_seconds,
                ),
            )
        except TimeoutError as exc:
            if loop.time() >= deadline:
                raise ASRTimeoutError(
                    f"ASR total timeout exceeded ({self._config.total_timeout_seconds}s)"
                ) from exc
            raise ASRAdmissionTimeoutError(
                f"ASR admission timed out after {self._config.admission_timeout_seconds}s"
            ) from exc

        try:
            async with self._lock:
                if not self.is_ready or self._is_shutting_down:
                    raise ASRNotReadyError("PhoWhisper runtime is shutting down")
                engine = self._engine
                assert engine is not None

                async def _worker() -> ASRResult:
                    try:
                        raw_text, segments, meta = await loop.run_in_executor(
                            self._executor, engine.run_inference, pcm_arr, target_lang
                        )
                        norm_text = normalize_transcript(raw_text)
                        return ASRResult(
                            raw_text=raw_text,
                            normalized_text=norm_text,
                            language=target_lang,
                            duration_seconds=duration_seconds,
                            segments=segments,
                            provider_metadata=meta,
                        )
                    finally:
                        self._semaphore.release()

                # Register under the lifecycle lock so shutdown cannot miss this worker.
                worker_task = asyncio.create_task(_worker())
                self._active_tasks.add(worker_task)
        except BaseException:
            self._semaphore.release()
            raise

        worker_task.add_done_callback(self._active_tasks.discard)

        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                self._is_ready = False
                raise ASRTimeoutError(
                    f"ASR total timeout exceeded ({self._config.total_timeout_seconds}s)"
                )
            res = await asyncio.wait_for(
                asyncio.shield(worker_task),
                timeout=remaining,
            )
            return res
        except TimeoutError as exc:
            # The native call cannot be killed safely. Stop admitting work and fail
            # readiness until the process is restarted after the worker exits.
            self._is_ready = False
            raise ASRTimeoutError(
                f"ASR total timeout exceeded ({self._config.total_timeout_seconds}s)"
            ) from exc
        except asyncio.CancelledError:
            raise
