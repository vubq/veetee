from __future__ import annotations

import asyncio
import json
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
                raise FileNotFoundError("VieNeu model is incomplete; run npm run models:prepare")
            # Instantiate the lite engine directly so every codec graph is read
            # from the pinned local model directory. The upstream Vieneu facade
            # currently does not forward ``codec_dir`` to its ONNX constructor
            # and otherwise performs Hugging Face HEAD requests on startup.
            import vieneu  # type: ignore[import-untyped]
            from vieneu._v3_turbo_engine.onnx_runtime_lite import (  # type: ignore[import-untyped]
                OnnxV3LiteEngine,
            )

            assert vieneu.__file__ is not None
            voices_path = Path(vieneu.__file__).parent / "assets" / "voices_v3_turbo.json"
            voices = json.loads(voices_path.read_text(encoding="utf-8"))
            self._engine = _LocalStreamingV3Engine(
                OnnxV3LiteEngine(
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

    @staticmethod
    def _float_to_pcm(samples: np.ndarray[Any, Any]) -> bytes:
        if samples.size == 0:
            return b""
        clipped = np.clip(samples, -1.0, 1.0)
        return bytes((clipped * 32767.0).astype("<i2").tobytes())


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
        self, text: str, *, voice: str, apply_watermark: bool
    ) -> Iterator[np.ndarray[Any, Any]]:
        from vieneu_utils.phonemize_text import (  # type: ignore[import-untyped]
            normalize_to_chunks_v3,
            phonemize_text_with_emotions,
        )

        preset = self._voices[voice]
        speaker_emb = np.asarray(preset["speaker_emb"], dtype=np.float32)
        ref_codes = np.asarray(preset["codes"], dtype=np.int64)
        style = str(preset.get("style", "tu_nhien"))
        for chunk in normalize_to_chunks_v3(text, max_chars=256):
            phonemes = phonemize_text_with_emotions(chunk)
            for audio in self._engine.infer_stream(
                phonemes=phonemes,
                speaker_emb=speaker_emb,
                ref_codes=ref_codes,
                style=style,
                use_ref_codes=True,
            ):
                if audio is None or len(audio) == 0:
                    continue
                if apply_watermark and self._watermarker is not None:
                    audio = self._watermarker.apply(audio, 48_000)
                yield np.asarray(audio, dtype=np.float32)
