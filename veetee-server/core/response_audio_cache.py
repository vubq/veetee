import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import logging
import time
from typing import Optional

from config.settings import TTSConfig
from core.providers.tts.base import BaseTTS, open_tts_stream


logger = logging.getLogger("ResponseAudioCache")

CACHE_SCHEMA_VERSION = 1
MAX_CACHE_ENTRIES = 16
MAX_CACHE_BYTES = 4 * 1024 * 1024
MAX_CLIP_SECONDS = 5.0


@dataclass(frozen=True)
class AudioCacheKey:
    schema_version: int
    text: str
    provider: str
    engine_identity: str
    voice: str
    source_voice: str
    sample_rate: int
    frame_duration_ms: int
    denoise: bool
    temperature: float


@dataclass(frozen=True)
class AudioCacheResult:
    frames: tuple[bytes, ...]
    hit: bool
    synthesis_ms: float


class ResponseAudioCache:
    """Small bounded RAM cache for configured fixed TTS responses."""

    def __init__(self, tts_engine: BaseTTS, tts_config: TTSConfig):
        self.tts_engine = tts_engine
        self.tts_config = tts_config
        self._entries: OrderedDict[AudioCacheKey, tuple[bytes, ...]] = OrderedDict()
        self._entry_bytes: dict[AudioCacheKey, int] = {}
        self._total_bytes = 0
        self._inflight: dict[AudioCacheKey, asyncio.Task] = {}
        self._inflight_priority: dict[AudioCacheKey, str] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def make_key(self, text: str) -> AudioCacheKey:
        engine_identity = getattr(self.tts_engine, "model", None) or type(self.tts_engine).__name__
        return AudioCacheKey(
            schema_version=CACHE_SCHEMA_VERSION,
            text=str(text or "").strip(),
            provider=str(self.tts_config.provider),
            engine_identity=str(engine_identity),
            voice=str(getattr(self.tts_engine, "voice", self.tts_config.voice)),
            source_voice=str(getattr(self.tts_engine, "source_voice", self.tts_config.source_voice)),
            sample_rate=int(getattr(self.tts_engine, "sample_rate", self.tts_config.sample_rate)),
            frame_duration_ms=int(
                getattr(self.tts_engine, "frame_duration_ms", self.tts_config.frame_duration_ms)
            ),
            denoise=bool(getattr(self.tts_engine, "denoise", self.tts_config.denoise)),
            temperature=float(getattr(self.tts_engine, "temperature", self.tts_config.temperature)),
        )

    async def get_or_fill(
        self,
        text: str,
        timeout_seconds: float,
        *,
        priority: str = "live",
    ) -> AudioCacheResult:
        if self._closed:
            raise RuntimeError("response audio cache is closed")
        key = self.make_key(text)
        if not key.text:
            raise ValueError("cannot cache an empty fixed response")

        async with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                logger.info("fixed_audio_cache hit text=%r frames=%d", key.text, len(cached))
                return AudioCacheResult(cached, True, 0.0)

            task = self._inflight.get(key)
            existing_priority = self._inflight_priority.get(key)
            if task is not None and priority == "live" and existing_priority == "prewarm":
                task.cancel()
                task = None
            if task is None:
                task = asyncio.create_task(self._fill(key, priority=priority))
                self._inflight[key] = task
                self._inflight_priority[key] = priority
                logger.info("fixed_audio_cache miss/fill text=%r", key.text)
            else:
                logger.info("fixed_audio_cache wait_existing_fill text=%r", key.text)

        try:
            frames, synthesis_ms = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=float(timeout_seconds),
            )
            return AudioCacheResult(frames, False, synthesis_ms)
        except asyncio.TimeoutError:
            logger.warning("fixed_audio_cache waiter timeout text=%r", key.text)
            raise

    async def _fill(self, key: AudioCacheKey, *, priority: str) -> tuple[tuple[bytes, ...], float]:
        started = time.perf_counter()
        cancel_event = asyncio.Event()
        frames: list[bytes] = []
        total_bytes = 0
        max_frames = max(1, int(MAX_CLIP_SECONDS * 1000 // max(1, key.frame_duration_ms)))
        try:
            async for frame in open_tts_stream(
                self.tts_engine,
                key.text,
                cancel_event,
                priority=priority,
                queue_deadline_seconds=None,
            ):
                payload = bytes(frame)
                if not payload:
                    continue
                frames.append(payload)
                total_bytes += len(payload)
                if len(frames) > max_frames:
                    raise ValueError(f"fixed response exceeds {MAX_CLIP_SECONDS:.0f}s cache clip limit")
                if total_bytes > MAX_CACHE_BYTES:
                    raise ValueError("fixed response exceeds cache byte limit")

            if not frames:
                raise RuntimeError("TTS produced no audio for fixed response")

            immutable_frames = tuple(frames)
            synthesis_ms = (time.perf_counter() - started) * 1000.0
            async with self._lock:
                if not self._closed:
                    self._publish(key, immutable_frames, total_bytes)
            logger.info(
                "fixed_audio_cache fill_complete text=%r frames=%d bytes=%d cold_ms=%.0f",
                key.text,
                len(immutable_frames),
                total_bytes,
                synthesis_ms,
            )
            return immutable_frames, synthesis_ms
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        except Exception:
            logger.exception("fixed_audio_cache fill_error text=%r", key.text)
            raise
        finally:
            async with self._lock:
                current = self._inflight.get(key)
                if current is asyncio.current_task():
                    self._inflight.pop(key, None)
                    self._inflight_priority.pop(key, None)

    def _publish(self, key: AudioCacheKey, frames: tuple[bytes, ...], total_bytes: int) -> None:
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._total_bytes -= self._entry_bytes.pop(key, 0)
        self._entries[key] = frames
        self._entry_bytes[key] = total_bytes
        self._total_bytes += total_bytes
        while len(self._entries) > MAX_CACHE_ENTRIES or self._total_bytes > MAX_CACHE_BYTES:
            old_key, _ = self._entries.popitem(last=False)
            self._total_bytes -= self._entry_bytes.pop(old_key, 0)

    async def prewarm(self, texts: list[str], timeout_seconds: float) -> None:
        unique_texts = []
        seen = set()
        for text in texts:
            normalized = str(text or "").strip()
            if normalized and normalized not in seen:
                unique_texts.append(normalized)
                seen.add(normalized)
            if len(unique_texts) >= 2:
                break
        if not unique_texts:
            return
        # Keep startup prewarm serialized through the same TTS path as normal
        # responses. At most two configured clips are attempted, each with its
        # own bounded waiter timeout.
        for text in unique_texts:
            try:
                await self.get_or_fill(text, timeout_seconds, priority="prewarm")
            except Exception as exc:
                logger.warning("fixed_audio_cache prewarm_failed text=%r error=%s", text, exc)

    async def shutdown(self) -> None:
        self._closed = True
        async with self._lock:
            tasks = list(self._inflight.values())
            self._inflight.clear()
            self._inflight_priority.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot(self) -> dict:
        return {
            "closed": self._closed,
            "entries": len(self._entries),
            "bytes": self._total_bytes,
            "inflight": len(self._inflight),
            "inflight_priorities": {
                priority: sum(1 for value in self._inflight_priority.values() if value == priority)
                for priority in {"live", "dashboard", "prewarm"}
            },
        }
