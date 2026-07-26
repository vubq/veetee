from __future__ import annotations

import asyncio
import ctypes
import json
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from time import monotonic
from typing import Any, Literal

import numpy as np
import soxr  # type: ignore[import-untyped]
import structlog
from audiotsm import wsola  # type: ignore[import-untyped]
from audiotsm.io.array import ArrayReader, ArrayWriter  # type: ignore[import-untyped]

from veetee_voice_server.conversation.cancellation import (
    OperationContext,
    await_operation,
)
from veetee_voice_server.conversation.sentence_chunker import TtsTextChunkingPolicy
from veetee_voice_server.conversation.types import AudioChunk

logger = structlog.get_logger(__name__)


class VieNeuTtsProvider:
    source_sample_rate = 48_000

    def __init__(
        self,
        model_dir: Path,
        *,
        voice: str,
        style: Literal["tu_nhien", "doc_truyen", "tin_tuc"] = "tu_nhien",
        speed: float = 1.0,
        pitch_hz: float = 0.0,
        volume: float = 1.0,
        output_sample_rate: int = 24_000,
        num_threads: int = 4,
        apply_watermark: bool = True,
        stream_leadin_frames: int = 16,
        engine: Any | None = None,
        inference_lock: asyncio.Lock | None = None,
        turn_lock: asyncio.Lock | None = None,
        turn_scope_active: ContextVar[bool] | None = None,
        worker_lock: threading.Lock | None = None,
        backend: Literal["onnx", "native"] = "onnx",
        native_model_dir: Path | None = None,
        native_library_path: Path | None = None,
        native_realtime_headroom: float = 1.15,
        native_use_ref_codes: bool = True,
    ) -> None:
        self._model_dir = model_dir
        self._voice = voice
        self._style = style
        if speed <= 0:
            raise ValueError("TTS speed must be greater than zero")
        self._speed = speed
        if pitch_hz:
            raise ValueError("VieNeu local TTS does not support pitch adjustment")
        if not 0 <= volume <= 1.5:
            raise ValueError("TTS volume must be between 0 and 1.5")
        self._volume = volume
        self._output_sample_rate = output_sample_rate
        self._num_threads = num_threads
        self._apply_watermark = apply_watermark
        if not 4 <= stream_leadin_frames <= 25:
            raise ValueError("VieNeu stream lead-in must contain 4 to 25 acoustic frames")
        self._stream_leadin_frames = stream_leadin_frames
        self._backend: Literal["onnx", "native"] = backend
        self._native_model_dir = native_model_dir
        self._native_library_path = native_library_path
        if native_realtime_headroom < 1.0:
            raise ValueError("VieNeu native realtime headroom must be at least 1.0")
        self._native_realtime_headroom = native_realtime_headroom
        self._native_use_ref_codes = native_use_ref_codes
        self._engine = engine
        self._load_lock = threading.Lock()
        self._inference_lock = inference_lock or asyncio.Lock()
        self._turn_lock = turn_lock or asyncio.Lock()
        self._turn_scope_active = turn_scope_active or ContextVar(
            f"vieneu_turn_scope_{id(self)}",
            default=False,
        )
        self._worker_lock = worker_lock or threading.Lock()
        self._conversion_samples = 0
        self._conversion_clipped_samples = 0
        for warning in self.quality_warnings:
            logger.warning(
                "vieneu_profile_quality_warning",
                code=warning,
                voice=self._voice,
                speed=self._speed,
                volume=self._volume,
            )

    @property
    def text_chunking_policy(self) -> TtsTextChunkingPolicy:
        # ONNX starts returning audio before inference completes, so it can safely
        # accept a longer emergency request than the batch-only native backend.
        sentence_batch_max = 160 if self._backend == "onnx" else 72
        return TtsTextChunkingPolicy(
            mode="sentence_bounded",
            emergency_max_characters=256 if self._backend == "onnx" else 72,
            sentence_batch_max_characters=sentence_batch_max,
        )

    @property
    def preferred_text_chunk_characters(self) -> int:
        # Native remains faster than playback around one natural clause while
        # keeping the next batch ready before the current audio drains.
        return 48 if self._backend == "native" else 72

    @property
    def maximum_text_chunk_characters(self) -> int:
        return 72 if self._backend == "native" else 112

    @property
    def initial_text_chunk_characters(self) -> int:
        return 24

    @property
    def initial_maximum_text_chunk_characters(self) -> int:
        return 40

    @property
    def quality_warnings(self) -> tuple[str, ...]:
        warnings: list[str] = []
        if self._speed >= 1.2:
            warnings.append("postprocess_rate_starvation_risk")
        if self._volume > 1.0:
            warnings.append("amplification_clipping_risk")
        return tuple(warnings)

    def with_profile(
        self,
        *,
        voice: str,
        style: Literal["tu_nhien", "doc_truyen", "tin_tuc"] = "tu_nhien",
        speed: float,
        pitch_hz: float = 0.0,
        volume: float = 1.0,
    ) -> VieNeuTtsProvider:
        """Create a profile view while reusing the prewarmed local engine."""
        return VieNeuTtsProvider(
            self._model_dir,
            voice=voice,
            style=style,
            speed=speed,
            pitch_hz=pitch_hz,
            volume=volume,
            output_sample_rate=self._output_sample_rate,
            num_threads=self._num_threads,
            apply_watermark=self._apply_watermark,
            stream_leadin_frames=self._stream_leadin_frames,
            engine=self._engine,
            inference_lock=self._inference_lock,
            turn_lock=self._turn_lock,
            turn_scope_active=self._turn_scope_active,
            worker_lock=self._worker_lock,
            backend=self._backend,
            native_model_dir=self._native_model_dir,
            native_library_path=self._native_library_path,
            native_realtime_headroom=self._native_realtime_headroom,
            native_use_ref_codes=self._native_use_ref_codes,
        )

    async def prewarm(self) -> None:
        await asyncio.to_thread(self._load_engine)

    async def close(self) -> None:
        engine = self._engine
        close = getattr(engine, "close", None)
        if close is not None:
            await asyncio.to_thread(self._close_serialized, close)

    @asynccontextmanager
    async def speech_turn(self, context: OperationContext) -> AsyncGenerator[None]:
        """Reserve the single local engine for one uninterrupted spoken turn."""
        if self._turn_scope_active.get():
            yield
            return
        async with _operation_lock(self._turn_lock, context):
            token = self._turn_scope_active.set(True)
            try:
                yield
            finally:
                self._turn_scope_active.reset(token)

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        del locale
        if not text.strip():
            return
        context.checkpoint()
        async with self._synthesis_access(context):
            started_at = monotonic()
            self._conversion_samples = 0
            self._conversion_clipped_samples = 0
            engine = await asyncio.to_thread(self._load_engine)
            inference_options = {
                "voice": self._voice,
                "style": self._style,
                "apply_watermark": self._apply_watermark,
            }
            if self._backend == "native":
                inference_options["use_ref_codes"] = self._native_use_ref_codes
            stream = await asyncio.to_thread(
                engine.infer_stream,
                text,
                **inference_options,
            )
            resampler = soxr.ResampleStream(
                self.source_sample_rate,
                self._output_sample_rate,
                1,
                dtype="float32",
                quality="HQ",
            )
            effective_speed = self._speed
            native_realtime_speed_ceiling: float | None = None
            tempo = (
                _StreamingTempo(self._speed)
                if self._speed != 1.0
                else None
            )
            sequence = 0
            try:
                while True:
                    has_chunk, source = await self._next_chunk_cancellation_safe(
                        stream,
                        context,
                    )
                    if not has_chunk:
                        break
                    context.checkpoint()
                    assert source is not None
                    if self._backend == "native":
                        source_seconds = source.size / self.source_sample_rate
                        synthesis_seconds = max(monotonic() - started_at, 0.001)
                        native_realtime_speed_ceiling = source_seconds / (
                            synthesis_seconds * self._native_realtime_headroom
                        )
                    if tempo is not None:
                        source = tempo.process(source)
                        if source.size == 0:
                            continue
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
                if tempo is not None:
                    source = tempo.process(np.empty(0, dtype=np.float32), final=True)
                    if source.size:
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
            finally:
                clipping_ratio = (
                    self._conversion_clipped_samples / self._conversion_samples
                    if self._conversion_samples
                    else 0.0
                )
                logger.info(
                    "vieneu_tts_completed",
                    voice=self._voice,
                    style=self._style,
                    requested_speed=self._speed,
                    effective_speed=round(effective_speed, 3),
                    realtime_speed_ceiling=(
                        round(native_realtime_speed_ceiling, 3)
                        if native_realtime_speed_ceiling is not None
                        else None
                    ),
                    realtime_headroom_met=(
                        self._speed <= native_realtime_speed_ceiling
                        if native_realtime_speed_ceiling is not None
                        else None
                    ),
                    backend=self._backend,
                    volume=self._volume,
                    duration_ms=round((monotonic() - started_at) * 1_000, 1),
                    audio_ms=round(
                        self._conversion_samples * 1_000 / self._output_sample_rate,
                        1,
                    ),
                    clipping_ratio=round(clipping_ratio, 6),
                )

    @asynccontextmanager
    async def _synthesis_access(
        self, context: OperationContext
    ) -> AsyncGenerator[None]:
        if self._turn_scope_active.get():
            async with self._inference_lock:
                yield
            return
        async with _operation_lock(self._turn_lock, context):
            async with self._inference_lock:
                yield

    def _load_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        with self._load_lock:
            if self._engine is not None:
                return self._engine
            if self._backend == "native":
                if self._native_model_dir is None or self._native_library_path is None:
                    raise ValueError(
                        "VieNeu native backend requires model and library paths"
                    )
                self._engine = _NativeVieNeuEngine(
                    self._native_library_path,
                    self._native_model_dir,
                    num_threads=self._num_threads,
                )
                available = {
                    voice_id for _, voice_id in self._engine.list_preset_voices()
                }
                if self._voice not in available:
                    choices = sorted(available)
                    raise ValueError(
                        f"VieNeu voice {self._voice!r} is unavailable; choose one of {choices}"
                    )
                return self._engine
            onnx_dir = self._model_dir / "onnx_int8"
            codec_dir = self._model_dir / "codec"
            if not onnx_dir.is_dir() or not codec_dir.is_dir():
                raise FileNotFoundError("VieNeu model is incomplete; run npm run models:prepare")
            # Instantiate the lite engine directly so every codec graph is read
            # from the pinned local model directory. The upstream Vieneu facade
            # currently does not forward ``codec_dir`` to its ONNX constructor
            # and otherwise performs Hugging Face HEAD requests on startup.
            import vieneu  # type: ignore[import-untyped]
            from vieneu._v3_turbo_engine import onnx_runtime_lite  # type: ignore[import-untyped]

            assert vieneu.__file__ is not None
            voices_path = Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"
            voices = json.loads(voices_path.read_text(encoding="utf-8"))
            _configure_stream_leadin(
                onnx_runtime_lite,
                self._stream_leadin_frames,
            )
            self._engine = _LocalStreamingV3Engine(
                onnx_runtime_lite.OnnxV3LiteEngine(
                    checkpoint_path=str(self._model_dir),
                    onnx_dir=str(onnx_dir),
                    codec_dir=str(codec_dir),
                    threads=self._num_threads,
                ),
                voices,
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

    def _next_chunk_serialized(
        self,
        stream: Iterator[Any],
        context: OperationContext,
    ) -> tuple[bool, np.ndarray[Any, Any] | None]:
        with self._worker_lock:
            context.checkpoint()
            return self._next_chunk(stream)

    async def _next_chunk_cancellation_safe(
        self,
        stream: Iterator[Any],
        context: OperationContext,
    ) -> tuple[bool, np.ndarray[Any, Any] | None]:
        worker = asyncio.create_task(
            asyncio.to_thread(self._next_chunk_serialized, stream, context)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Native C synthesis cannot be force-killed. Keep the provider locks
            # until the active call really exits so later turns cannot enqueue
            # abandoned work behind it, while the cancelled turn returns through
            # its detached generator task immediately.
            await asyncio.gather(worker, return_exceptions=True)
            raise

    def _close_serialized(self, close: Any) -> None:
        with self._worker_lock:
            close()

    def _float_to_pcm(self, samples: np.ndarray[Any, Any]) -> bytes:
        if samples.size == 0:
            return b""
        amplified = samples * self._volume
        self._conversion_samples += int(amplified.size)
        self._conversion_clipped_samples += int(np.count_nonzero(np.abs(amplified) > 1.0))
        clipped = np.clip(amplified, -1.0, 1.0)
        return bytes((clipped * 32767.0).astype("<i2").tobytes())


@asynccontextmanager
async def _operation_lock(
    lock: asyncio.Lock,
    context: OperationContext,
) -> AsyncGenerator[None]:
    acquire_task = asyncio.create_task(lock.acquire())
    acquired = False
    try:
        try:
            await await_operation(acquire_task, context)
            context.checkpoint()
            acquired = True
        except BaseException:
            await asyncio.gather(acquire_task, return_exceptions=True)
            if (
                acquire_task.done()
                and not acquire_task.cancelled()
                and acquire_task.exception() is None
                and acquire_task.result()
            ):
                lock.release()
            raise
        yield
    finally:
        if acquired:
            lock.release()


def _configure_stream_leadin(module: Any, frames: int) -> None:
    """Tune the pinned VieNeu stream to build enough audio before playback starts."""
    current = getattr(module, "_STREAM_LEADIN_FRAMES", None)
    if not isinstance(current, int):
        raise RuntimeError("VieNeu 3.2.3 streaming lead-in contract is unavailable")
    module._STREAM_LEADIN_FRAMES = frames


class _NativeInitParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("profile", ctypes.c_char_p),
        ("model_dir", ctypes.c_char_p),
        ("onnx_dir", ctypes.c_char_p),
        ("codec_dir", ctypes.c_char_p),
        ("config_path", ctypes.c_char_p),
        ("tokenizer_path", ctypes.c_char_p),
        ("voices_json_path", ctypes.c_char_p),
        ("n_threads", ctypes.c_int),
    ]


class _NativeTtsParams(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("text", ctypes.c_char_p),
        ("voice_id", ctypes.c_char_p),
        ("ref_audio_path", ctypes.c_char_p),
        ("style", ctypes.c_char_p),
        ("temperature", ctypes.c_float),
        ("top_k", ctypes.c_int),
        ("top_p", ctypes.c_float),
        ("max_new_frames", ctypes.c_int),
        ("repetition_penalty", ctypes.c_float),
        ("max_chars", ctypes.c_int),
        ("denoise_ref", ctypes.c_bool),
        ("use_ref_codes", ctypes.c_bool),
        ("apply_watermark", ctypes.c_bool),
    ]


class _NativeAudio(ctypes.Structure):
    _fields_ = [
        ("samples", ctypes.POINTER(ctypes.c_float)),
        ("n_samples", ctypes.c_int),
        ("sample_rate", ctypes.c_int),
        ("channels", ctypes.c_int),
    ]


class _NativeVieNeuEngine:
    def __init__(
        self,
        library_path: Path,
        model_dir: Path,
        *,
        num_threads: int,
    ) -> None:
        if not library_path.is_file():
            raise FileNotFoundError(f"Missing VieNeu native library: {library_path}")
        required = (
            model_dir / "backbone.gguf",
            model_dir / "config.json",
            model_dir / "tokenizer.json",
            model_dir / "voices_v3_turbo.json",
            model_dir / "codec",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Missing VieNeu native assets: {missing}")

        self._library = ctypes.CDLL(str(library_path.resolve()))
        self._configure_abi()
        self._model_dir = model_dir.resolve()
        self._voices_path = self._model_dir / "voices_v3_turbo.json"
        voices_document = json.loads(self._voices_path.read_text(encoding="utf-8"))
        presets = voices_document.get("presets", {})
        if not isinstance(presets, dict):
            raise ValueError("VieNeu native voices catalog is invalid")
        self._voices = presets
        self._context = self._initialize(num_threads)
        self._closed = False

    def _configure_abi(self) -> None:
        self._library.vieneu_last_error.argtypes = []
        self._library.vieneu_last_error.restype = ctypes.c_char_p
        self._library.vieneu_init_v2_default_params.argtypes = [
            ctypes.POINTER(_NativeInitParams)
        ]
        self._library.vieneu_init_v2_default_params.restype = None
        self._library.vieneu_init_v2.argtypes = [ctypes.POINTER(_NativeInitParams)]
        self._library.vieneu_init_v2.restype = ctypes.c_void_p
        self._library.vieneu_tts_v3_default_params.argtypes = [
            ctypes.POINTER(_NativeTtsParams)
        ]
        self._library.vieneu_tts_v3_default_params.restype = None
        self._library.vieneu_synthesize_v3.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_NativeTtsParams),
            ctypes.POINTER(_NativeAudio),
        ]
        self._library.vieneu_synthesize_v3.restype = ctypes.c_int
        self._library.vieneu_audio_free.argtypes = [ctypes.POINTER(_NativeAudio)]
        self._library.vieneu_audio_free.restype = None
        self._library.vieneu_free.argtypes = [ctypes.c_void_p]
        self._library.vieneu_free.restype = None

    def _initialize(self, num_threads: int) -> int:
        params = _NativeInitParams()
        self._library.vieneu_init_v2_default_params(ctypes.byref(params))
        encoded = {
            "profile": b"vieneu-v3-native",
            "model_dir": str(self._model_dir).encode(),
            "codec_dir": str(self._model_dir / "codec").encode(),
            "voices": str(self._voices_path).encode(),
        }
        params.profile = encoded["profile"]
        params.model_dir = encoded["model_dir"]
        params.codec_dir = encoded["codec_dir"]
        params.voices_json_path = encoded["voices"]
        params.n_threads = num_threads
        context = self._library.vieneu_init_v2(ctypes.byref(params))
        if not context:
            raise RuntimeError(f"VieNeu native initialization failed: {self._last_error()}")
        return int(context)

    def list_preset_voices(self) -> list[tuple[str, str]]:
        return [(str(name), str(name)) for name in self._voices]

    def infer_stream(
        self,
        text: str,
        *,
        voice: str,
        style: str,
        apply_watermark: bool,
        use_ref_codes: bool,
    ) -> Iterator[np.ndarray[Any, Any]]:
        if self._closed:
            raise RuntimeError("VieNeu native engine is closed")
        preset = self._voices.get(voice)
        if not isinstance(preset, dict):
            raise ValueError(f"VieNeu native voice {voice!r} is unavailable")
        params = _NativeTtsParams()
        self._library.vieneu_tts_v3_default_params(ctypes.byref(params))
        encoded_text = text.encode()
        encoded_voice = voice.encode()
        encoded_style = style.encode()
        params.text = encoded_text
        params.voice_id = encoded_voice
        params.style = encoded_style
        params.use_ref_codes = use_ref_codes
        params.apply_watermark = apply_watermark
        audio = _NativeAudio()
        status = self._library.vieneu_synthesize_v3(
            self._context,
            ctypes.byref(params),
            ctypes.byref(audio),
        )
        if status != 0:
            raise RuntimeError(f"VieNeu native synthesis failed: {self._last_error()}")
        try:
            if (
                not audio.samples
                or audio.n_samples <= 0
                or audio.sample_rate != 48_000
                or audio.channels != 1
            ):
                raise RuntimeError("VieNeu native synthesis returned invalid mono 48 kHz audio")
            samples = np.ctypeslib.as_array(
                audio.samples,
                shape=(audio.n_samples,),
            ).copy()
        finally:
            self._library.vieneu_audio_free(ctypes.byref(audio))
        yield np.asarray(samples, dtype=np.float32)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._library.vieneu_free(self._context)

    def _last_error(self) -> str:
        raw = self._library.vieneu_last_error()
        return raw.decode(errors="replace") if raw else "unknown native error"


class _StreamingTempo:
    def __init__(self, speed: float) -> None:
        self._processor = wsola(channels=1, speed=speed)

    def process(
        self, samples: np.ndarray[Any, Any], *, final: bool = False
    ) -> np.ndarray[Any, np.dtype[np.float32]]:
        source = np.asarray(samples, dtype=np.float32).reshape(1, -1)
        writer = ArrayWriter(1)
        self._processor.run(ArrayReader(source), writer, flush=final)
        return np.asarray(writer.data[0], dtype=np.float32)


class _LocalStreamingV3Engine:
    def __init__(self, engine: Any, voices: dict[str, Any]) -> None:
        self._engine = engine
        self._voices = voices.get("presets", {})
        self._watermarker: Any | None = None
        try:
            import perth  # type: ignore[import-untyped]

            self._watermarker = perth.PerthImplicitWatermarker()
        except (ImportError, AttributeError):
            pass

    def list_preset_voices(self) -> list[tuple[str, str]]:
        return [(name, name) for name in self._voices]

    def infer_stream(
        self, text: str, *, voice: str, style: str, apply_watermark: bool
    ) -> Iterator[np.ndarray[Any, Any]]:
        from vieneu_utils.phonemize_text import (  # type: ignore[import-untyped]
            normalize_to_chunks_v3,
            phonemize_text_with_emotions,
        )

        preset = self._voices[voice]
        speaker_emb = np.asarray(preset["speaker_emb"], dtype=np.float32)
        ref_codes = np.asarray(preset["codes"], dtype=np.int64)
        resolved_style = style or str(preset.get("style", "tu_nhien"))
        for chunk in normalize_to_chunks_v3(text, max_chars=256):
            phonemes = phonemize_text_with_emotions(chunk)
            for audio in self._engine.infer_stream(
                phonemes=phonemes,
                speaker_emb=speaker_emb,
                ref_codes=ref_codes,
                style=resolved_style,
                use_ref_codes=True,
            ):
                if audio is None or len(audio) == 0:
                    continue
                if apply_watermark and self._watermarker is not None:
                    audio = self._watermarker.apply(audio, 48_000)
                yield np.asarray(audio, dtype=np.float32)
