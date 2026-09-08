import asyncio
import functools
import io
import logging
import threading
import time
import wave
import numpy as np
from typing import AsyncGenerator, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from core.providers.tts.base import BaseTTS
from core.providers.tts.scheduler import TTSAdmissionScheduler
from core.audio_utils import AudioCodec
from core.turn_metrics import mark_current

logger = logging.getLogger("VieneuTTS")

class VieneuLocalTTS(BaseTTS):
    def __init__(
        self,
        voice: str = "Xuân Vĩnh",
        source_voice: str = "Xuân Vĩnh",
        sample_rate: int = 24000,
        frame_duration_ms: int = 60,
        stream_queue_max_chunks: int = 4,
        denoise: bool = True,
        temperature: float = 0.7
    ):
        self.voice = voice
        self.source_voice = source_voice
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.stream_queue_max_chunks = max(1, int(stream_queue_max_chunks))
        self.denoise = denoise
        self.temperature = temperature
        
        self.codec = AudioCodec(
            in_sample_rate=16000,
            out_sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms
        )
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vieneu_tts")
        self._scheduler = TTSAdmissionScheduler()
        self._worker_futures = set()
        self.engine = None
        self._init_engine()

    def _get_scheduler(self) -> TTSAdmissionScheduler:
        """Return the shared scheduler, creating it for legacy/test instances."""
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is None:
            scheduler = TTSAdmissionScheduler()
            self._scheduler = scheduler
        return scheduler

    def _init_engine(self):
        logger.info(f"Loading local Vieneu Neural TTS engine (preset voice: '{self.voice}')...")
        t0 = time.time()
        from vieneu import Vieneu
        self.engine = Vieneu()

        # VieNeu v3 Turbo's native PyTorch streamer buffers 25 acoustic frames
        # before yielding by default. On the GTX 1650 Ti this dominates TTFA.
        # One acoustic frame gives the best measured TTFA on this GTX 1650 Ti.
        # VieNeu yields ~80 ms of 48 kHz audio, enough to emit the first 60 ms
        # Opus packet immediately while retaining the remainder for the next one.
        native_engine = getattr(self.engine, "engine", None)
        native_stream = getattr(native_engine, "infer_stream", None)
        if native_stream is not None and getattr(self.engine, "backend", None) == "pytorch":
            native_engine.infer_stream = functools.partial(native_stream, chunk_frames=1)

        # Warmup engine
        for _ in self.engine.infer_stream(
            "Xin chào",
            voice=self.voice,
            denoise=self.denoise,
            temperature=self.temperature,
            apply_watermark=False,
        ):
            break
        logger.info(f"Vieneu Neural TTS loaded and warmed up in {time.time() - t0:.2f}s")

    async def synthesize_wav(
        self,
        text: str,
        *,
        priority: str = "dashboard",
        queue_deadline_seconds: Optional[float] = 30.0,
    ) -> bytes:
        """Generate a complete mono WAV for dashboard source-audio testing."""
        text = (text or "").strip()
        if not text:
            return b""

        loop = asyncio.get_running_loop()

        lease = await self._get_scheduler().acquire(
            priority,
            deadline_seconds=queue_deadline_seconds,
        )
        mark_current("tts_lock_acquired", priority=lease.priority, queue_wait_ms=round(lease.wait_ms, 3))

        def _generate_wav() -> bytes:
            audio_48k = self.engine.infer(
                text,
                voice=self.source_voice,
                denoise=self.denoise,
                temperature=self.temperature,
                apply_watermark=False,
            )
            pcm_24k = self.codec.resample_float32_48k_to_pcm16_24k(audio_48k)
            output = io.BytesIO()
            with wave.open(output, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(pcm_24k.tobytes())
            return output.getvalue()

        try:
            return await loop.run_in_executor(self.executor, _generate_wav)
        finally:
            await lease.release()

    async def stream_sentence_to_opus(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
        *,
        priority: str = "live",
        queue_deadline_seconds: Optional[float] = None,
    ) -> AsyncGenerator[bytes, None]:
        if not text or not text.strip():
            return
        
        remainder_buffer = bytearray()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.stream_queue_max_chunks)
        queue_done = object()
        stop_event = threading.Event()
        lease = await self._get_scheduler().acquire(
            priority,
            cancel_event=cancel_event,
            deadline_seconds=queue_deadline_seconds,
        )
        mark_current("tts_lock_acquired", priority=lease.priority, queue_wait_ms=round(lease.wait_ms, 3))

        def _put_with_backpressure(item) -> bool:
            if stop_event.is_set():
                return False
            put_future = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            while True:
                try:
                    put_future.result(timeout=0.1)
                    return True
                except FutureTimeoutError:
                    if stop_event.is_set() or (cancel_event and cancel_event.is_set()):
                        put_future.cancel()
                        return False
                except Exception as exc:
                    logger.debug("TTS queue bridge stopped: %s", exc)
                    return False

        def _generate():
            try:
                for chunk in self.engine.infer_stream(
                    text,
                    voice=self.voice,
                    denoise=self.denoise,
                    temperature=self.temperature,
                    apply_watermark=False,
                ):
                    if cancel_event and cancel_event.is_set():
                        break
                    if not _put_with_backpressure(chunk):
                        break
            except Exception as e:
                logger.error(f"Error during Vieneu infer_stream: {e}")
            finally:
                if not stop_event.is_set() and not (cancel_event and cancel_event.is_set()):
                    _put_with_backpressure(queue_done)

        worker_future = loop.run_in_executor(self.executor, _generate)
        self._worker_futures.add(worker_future)

        def _worker_done(future):
            self._worker_futures.discard(future)
            try:
                future.result()
            except Exception as exc:
                logger.debug("TTS worker finished with error: %s", exc)

        worker_future.add_done_callback(_worker_done)

        try:
            first_pcm = True
            first_opus = True
            while True:
                if cancel_event and cancel_event.is_set():
                    break

                if cancel_event is None:
                    chunk = await queue.get()
                else:
                    get_task = asyncio.create_task(queue.get())
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        (get_task, cancel_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_task in done and cancel_event.is_set():
                        get_task.cancel()
                        try:
                            await get_task
                        except asyncio.CancelledError:
                            pass
                        break
                    cancel_task.cancel()
                    try:
                        await cancel_task
                    except asyncio.CancelledError:
                        pass
                    chunk = get_task.result()

                if chunk is queue_done:
                    break

                if first_pcm:
                    mark_current("tts_first_pcm", priority=lease.priority)
                    first_pcm = False

                pcm_24k = self.codec.resample_float32_48k_to_pcm16_24k(chunk)
                opus_frames = self.codec.chunk_pcm_to_opus_frames(pcm_24k, remainder_buffer)
                for frame in opus_frames:
                    if cancel_event and cancel_event.is_set():
                        break
                    if first_opus:
                        mark_current("tts_first_opus", priority=lease.priority)
                        first_opus = False
                    yield frame

            if not (cancel_event and cancel_event.is_set()):
                final_frames = self.codec.flush_remainder_to_opus_frame(remainder_buffer)
                for frame in final_frames:
                    if first_opus:
                        mark_current("tts_first_opus", priority=lease.priority)
                        first_opus = False
                    yield frame
        finally:
            stop_event.set()
            if not worker_future.done():
                await asyncio.shield(worker_future)
            await lease.release()

    def scheduler_snapshot(self) -> dict:
        return self._get_scheduler().snapshot()
