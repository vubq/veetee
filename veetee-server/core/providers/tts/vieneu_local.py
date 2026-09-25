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
from core.providers.tts.scheduler import TTSAdmissionScheduler, TTSPreempted
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
        first_audio_priority_boost: float = 5.0,
        scheduler_aging_per_second: float = 2.0,
        native_chunk_frames: int = 1,
        denoise: bool = True,
        temperature: float = 0.7,
        admission_timeout_ms: int = 5000,
        first_chunk_timeout_ms: int = 4000,
        stall_timeout_ms: int = 2500,
        voice_state_path: Optional[str] = None
    ):
        self.voice_state_path = voice_state_path
        self.voice = voice
        self.source_voice = source_voice
        saved = self._load_saved_voice()
        if saved:
            self.voice = saved
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.stream_queue_max_chunks = max(1, int(stream_queue_max_chunks))
        self.first_audio_priority_boost = max(
            0.0, float(first_audio_priority_boost)
        )
        self.scheduler_aging_per_second = max(
            0.1, float(scheduler_aging_per_second)
        )
        self.native_chunk_frames = max(1, int(native_chunk_frames))
        self.denoise = denoise
        self.temperature = temperature
        self.admission_timeout_ms = max(100, int(admission_timeout_ms))
        self.first_chunk_timeout_ms = max(100, int(first_chunk_timeout_ms))
        self.stall_timeout_ms = max(100, int(stall_timeout_ms))
        
        self.codec = AudioCodec(
            in_sample_rate=16000,
            out_sample_rate=sample_rate,
            frame_duration_ms=frame_duration_ms
        )
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vieneu_tts")
        self._scheduler = TTSAdmissionScheduler(
            aging_priority_per_second=self.scheduler_aging_per_second,
            first_audio_priority_boost=self.first_audio_priority_boost,
        )
        self._worker_futures = set()
        self._detached_cleanup_tasks = set()
        self._quarantined_worker = None
        self._closed = False
        self.engine = None
        self._init_engine()

    _preset_voice_cache: Optional[list] = None

    @classmethod
    def list_preset_voices(cls) -> list:
        """[(description, name)] from the engine; [] if unavailable.

        Cached process-wide: instantiating Vieneu just to list static
        presets would reload weights on every validation call (and risk
        a second GPU copy next to the running engine).
        """
        if cls._preset_voice_cache is None:
            try:
                from vieneu import Vieneu
                cls._preset_voice_cache = list(Vieneu().list_preset_voices())
            except Exception as exc:
                logger.warning(f"Could not list Vieneu preset voices: {exc}")
                return []
        return cls._preset_voice_cache

    def available_voices(self) -> list:
        """Prefer the already-loaded engine; no extra GPU copy."""
        engine = getattr(self, "engine", None)
        if engine is not None and hasattr(engine, "list_preset_voices"):
            try:
                return list(engine.list_preset_voices())
            except Exception as exc:
                logger.warning(f"Could not list voices from engine: {exc}")
        return self.list_preset_voices()

    def _load_saved_voice(self) -> str:
        # Runs before the engine loads, so it must not touch the engine
        # (that would load model weights twice). Trust our own state file;
        # set_voice() validates against the engine list at change time.
        if not self.voice_state_path:
            return ""
        try:
            with open(self.voice_state_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""
        except OSError as e:
            logger.warning(f"Could not read saved voice: {e}")
            return ""

    def set_voice(self, voice: str, persist: bool = True) -> str:
        """Switch reply voice immediately; persists across restarts."""
        cleaned = (voice or "").strip()
        if not cleaned:
            raise ValueError("voice must not be empty")
        names = {name for _, name in self.available_voices()}
        if names and cleaned not in names:
            raise ValueError(f"unknown voice: {cleaned!r}")
        self.voice = cleaned
        if persist and self.voice_state_path:
            import os
            state_dir = os.path.dirname(self.voice_state_path)
            os.makedirs(state_dir, exist_ok=True)
            temp_path = f"{self.voice_state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            os.replace(temp_path, self.voice_state_path)
        return self.voice

    def _get_scheduler(self) -> TTSAdmissionScheduler:
        """Return the shared scheduler, creating it for legacy/test instances."""
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is None:
            scheduler = TTSAdmissionScheduler(
                aging_priority_per_second=float(
                    getattr(self, "scheduler_aging_per_second", 2.0)
                ),
                first_audio_priority_boost=float(
                    getattr(self, "first_audio_priority_boost", 5.0)
                ),
            )
            self._scheduler = scheduler
        return scheduler

    def set_scheduler_policy(
        self,
        *,
        first_audio_priority_boost: Optional[float] = None,
        scheduler_aging_per_second: Optional[float] = None,
    ) -> None:
        if first_audio_priority_boost is not None:
            self.first_audio_priority_boost = max(
                0.0, float(first_audio_priority_boost)
            )
        if scheduler_aging_per_second is not None:
            self.scheduler_aging_per_second = max(
                0.1, float(scheduler_aging_per_second)
            )
        self._get_scheduler().configure(
            first_audio_priority_boost=self.first_audio_priority_boost,
            aging_priority_per_second=self.scheduler_aging_per_second,
        )

    def _submit_worker(self, loop: asyncio.AbstractEventLoop, func):
        """Submit one inference worker and keep it visible to shutdown."""
        if getattr(self, "_closed", False):
            raise RuntimeError("Vieneu TTS is shut down")
        future = loop.run_in_executor(self.executor, func)
        self._worker_futures.add(future)

        def _worker_done(done_future):
            self._worker_futures.discard(done_future)
            try:
                done_future.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("TTS worker finished with error: %s", exc)

        future.add_done_callback(_worker_done)
        return future

    def _ensure_engine_available(self) -> None:
        """Reject new inference while a cancelled native call is still running."""
        if getattr(self, "_closed", False):
            raise RuntimeError("Vieneu TTS is shut down")
        quarantined = getattr(self, "_quarantined_worker", None)
        if quarantined is not None and not quarantined.done():
            raise RuntimeError(
                "Vieneu TTS engine is still stopping a cancelled inference"
            )

    def _track_worker_lease_release(
        self,
        worker_future,
        lease,
        acquired_perf: float,
    ) -> asyncio.Task:
        """Release the engine lease when native inference actually finishes.

        The lease protects the single native TTS engine, not downstream Opus
        conversion or paced playback. Once the worker has materialized all
        chunks into the bounded queue, another session may safely use the
        engine while the previous session drains buffered audio.

        If the consumer stops before the worker exits, the caller marks that
        worker as quarantined. This tracker then clears the quarantine only
        after native inference has really stopped.
        """

        async def _finish_and_release():
            try:
                await asyncio.shield(worker_future)
            except Exception as exc:
                logger.debug("Detached Vieneu worker finished with error: %s", exc)
            finally:
                held_ms = (time.perf_counter() - acquired_perf) * 1000.0
                mark_current(
                    "tts_lease_held",
                    held_ms=round(held_ms, 3),
                    priority=lease.priority,
                    queue_wait_ms=round(lease.wait_ms, 3),
                )
                await lease.release()
                if getattr(self, "_quarantined_worker", None) is worker_future:
                    self._quarantined_worker = None

        task = asyncio.create_task(_finish_and_release())
        cleanup_tasks = getattr(self, "_detached_cleanup_tasks", None)
        if cleanup_tasks is None:
            cleanup_tasks = set()
            self._detached_cleanup_tasks = cleanup_tasks
        cleanup_tasks.add(task)
        task.add_done_callback(cleanup_tasks.discard)
        return task

    def _detach_worker_cleanup(self, worker_future, lease, acquired_perf: float) -> None:
        """Backward-compatible helper for tests/older call sites."""
        self._quarantined_worker = worker_future
        self._track_worker_lease_release(worker_future, lease, acquired_perf)

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
            native_engine.infer_stream = functools.partial(
                native_stream, chunk_frames=self.native_chunk_frames
            )

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
        self._ensure_engine_available()

        loop = asyncio.get_running_loop()

        lease = await self._get_scheduler().acquire(
            priority,
            deadline_seconds=queue_deadline_seconds,
        )
        mark_current("tts_lock_acquired", priority=lease.priority, queue_wait_ms=round(lease.wait_ms, 3))

        def _generate_wav() -> bytes:
            chunks = []
            for chunk in self.engine.infer_stream(
                text,
                voice=self.source_voice,
                denoise=self.denoise,
                temperature=self.temperature,
                apply_watermark=False,
            ):
                if lease.preempted:
                    raise TTSPreempted(
                        f"{lease.priority} TTS yielded to live voice"
                    )
                chunks.append(np.asarray(chunk, dtype=np.float32))
            if lease.preempted:
                raise TTSPreempted(
                    f"{lease.priority} TTS yielded to live voice"
                )
            if not chunks:
                raise RuntimeError("Vieneu TTS produced no audio")
            audio_48k = (
                chunks[0]
                if len(chunks) == 1
                else np.concatenate(chunks)
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
            return await self._submit_worker(loop, _generate_wav)
        except TTSPreempted:
            mark_current("tts_preempted", priority=lease.priority)
            raise
        finally:
            await lease.release()

    async def stream_sentence_to_opus(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None,
        *,
        priority: str = "live",
        queue_deadline_seconds: Optional[float] = None,
        initial_turn_audio: bool = True,
        voice_override: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        if not text or not text.strip():
            return
        self._ensure_engine_available()
        
        remainder_buffer = bytearray()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.stream_queue_max_chunks)
        queue_done = object()
        stop_event = threading.Event()
        worker_errors = []
        priority_name = str(priority or "live").strip().lower()
        first_chunk_budget_s = max(
            0.1,
            float(getattr(self, "first_chunk_timeout_ms", 4000)) / 1000.0,
        )
        admission_budget_s = queue_deadline_seconds
        if priority_name == "live_first":
            configured_admission_s = max(
                0.1,
                float(getattr(self, "admission_timeout_ms", 5000)) / 1000.0,
            )
            admission_budget_s = (
                configured_admission_s
                if admission_budget_s is None
                else min(float(admission_budget_s), configured_admission_s)
            )
        elif priority_name == "live" and admission_budget_s is None:
            # Continuation speech has already delivered first audio for the
            # turn. Do not fail the whole turn merely because another live
            # session is ahead of it; callers may give it the remaining turn
            # budget. Direct callers without a budget still get the normal
            # admission cap.
            admission_budget_s = max(
                0.1,
                float(getattr(self, "admission_timeout_ms", 5000)) / 1000.0,
            )
        first_chunk_deadline = None
        try:
            lease = await self._get_scheduler().acquire(
                priority_name,
                cancel_event=cancel_event,
                deadline_seconds=admission_budget_s,
            )
        except asyncio.TimeoutError as exc:
            timeout_ms = (
                float(admission_budget_s) * 1000.0
                if admission_budget_s is not None
                else float(getattr(self, "admission_timeout_ms", 5000))
            )
            mark_current(
                "tts_admission_timeout",
                priority=priority_name,
                timeout_ms=round(timeout_ms, 3),
            )
            raise TimeoutError(
                "Vieneu TTS admission timeout while waiting for engine "
                f"after {int(timeout_ms)} ms"
            ) from exc
        mark_current(
            "tts_lock_acquired",
            priority=lease.priority,
            queue_wait_ms=round(lease.wait_ms, 3),
            initial_turn_audio=bool(initial_turn_audio),
        )
        if priority_name in {"live_first", "live"}:
            # Scheduler wait and native first-PCM are distinct failure modes.
            # Once the engine is acquired, keep the first PCM budget strict so
            # a stuck model is detected quickly without misclassifying healthy
            # cross-session queueing as an inference timeout.
            first_chunk_deadline = loop.time() + first_chunk_budget_s

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
                    voice=(voice_override or self.voice),
                    denoise=self.denoise,
                    temperature=self.temperature,
                    apply_watermark=False,
                ):
                    if cancel_event and cancel_event.is_set():
                        break
                    if lease.preempted:
                        raise TTSPreempted(
                            f"{lease.priority} TTS yielded to live voice"
                        )
                    # Detach buffered audio from any engine-owned/native
                    # storage before releasing the single-engine lease.
                    buffered_chunk = np.asarray(
                        chunk, dtype=np.float32
                    ).copy()
                    if not _put_with_backpressure(buffered_chunk):
                        break
                if lease.preempted:
                    raise TTSPreempted(
                        f"{lease.priority} TTS yielded to live voice"
                    )
            except TTSPreempted as exc:
                mark_current("tts_preempted", priority=lease.priority)
                worker_errors.append(exc)
            except Exception as e:
                logger.error(f"Error during Vieneu infer_stream: {e}")
                worker_errors.append(e)
            finally:
                if not stop_event.is_set() and not (cancel_event and cancel_event.is_set()):
                    _put_with_backpressure(queue_done)

        acquired_perf = time.perf_counter()
        worker_future = self._submit_worker(loop, _generate)
        lease_release_task = self._track_worker_lease_release(
            worker_future,
            lease,
            acquired_perf,
        )

        try:
            first_pcm = True
            first_opus = True
            while True:
                if cancel_event and cancel_event.is_set():
                    break

                if first_pcm and first_chunk_deadline is not None:
                    remaining_first_chunk_s = first_chunk_deadline - loop.time()
                    if remaining_first_chunk_s <= 0:
                        raise TimeoutError(
                            "Vieneu TTS first chunk timeout after "
                            f"{getattr(self, 'first_chunk_timeout_ms', 4000)} ms"
                        )
                    timeout_ms = remaining_first_chunk_s * 1000.0
                else:
                    timeout_ms = (
                        getattr(self, "first_chunk_timeout_ms", 4000)
                        if first_pcm
                        else getattr(self, "stall_timeout_ms", 2500)
                    )
                get_task = asyncio.create_task(queue.get())
                cancel_task = (
                    asyncio.create_task(cancel_event.wait())
                    if cancel_event is not None
                    else None
                )
                waiters = (
                    (get_task, cancel_task)
                    if cancel_task is not None
                    else (get_task,)
                )
                try:
                    done, _pending = await asyncio.wait(
                        waiters,
                        timeout=max(0.1, timeout_ms / 1000.0),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except BaseException:
                    # If the stream consumer itself is cancelled while this
                    # wait is pending, asyncio.wait() does not cancel its child
                    # tasks. Drain them explicitly so Queue.get()/Event.wait()
                    # cannot survive the generator and trigger "Task was
                    # destroyed but it is pending" later.
                    for waiter in waiters:
                        if not waiter.done():
                            waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
                    raise
                if not done:
                    get_task.cancel()
                    if cancel_task is not None:
                        cancel_task.cancel()
                    await asyncio.gather(
                        *[
                            task
                            for task in (get_task, cancel_task)
                            if task is not None
                        ],
                        return_exceptions=True,
                    )
                    stage = "first chunk" if first_pcm else "stream stall"
                    raise TimeoutError(
                        f"Vieneu TTS {stage} timeout after {timeout_ms} ms"
                    )
                if (
                    cancel_task is not None
                    and cancel_task in done
                    and cancel_event.is_set()
                ):
                    get_task.cancel()
                    cancel_task.cancel()
                    await asyncio.gather(
                        get_task, cancel_task, return_exceptions=True
                    )
                    break
                if cancel_task is not None:
                    cancel_task.cancel()
                    await asyncio.gather(cancel_task, return_exceptions=True)
                chunk = get_task.result()

                if chunk is queue_done:
                    # queue_done is emitted from the worker's finally block.
                    # The thread may still need one event-loop tick before its
                    # Future becomes done(), so join it here before releasing
                    # the stream. This keeps scheduler ownership deterministic
                    # after a normally completed/preempted producer and avoids
                    # a transient active lease leaking past generator close.
                    await asyncio.shield(worker_future)
                    mark_current(
                        "tts_inference_done",
                        priority=lease.priority,
                        inference_ms=round(
                            (time.perf_counter() - acquired_perf) * 1000.0,
                            3,
                        ),
                    )
                    if worker_errors:
                        error = worker_errors[0]
                        if isinstance(error, TTSPreempted):
                            raise error
                        raise RuntimeError("Vieneu worker failed") from error
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
                # Native inference cannot be force-cancelled. Block new engine
                # users until the worker really exits; lease_release_task will
                # release the scheduler and clear this quarantine afterwards.
                self._quarantined_worker = worker_future
            else:
                await asyncio.gather(
                    asyncio.shield(lease_release_task),
                    return_exceptions=True,
                )

    def scheduler_snapshot(self) -> dict:
        return self._get_scheduler().snapshot()

    async def shutdown(self, *, grace_seconds: float = 5.0) -> None:
        """Stop accepting inference and release executor resources.

        Python cannot forcibly stop an inference function already executing in
        a worker thread. Normal server shutdown closes sessions/cache first, so
        active streaming workers should already be winding down here. We wait
        for them only for a bounded grace period and cancel work that has not
        started before shutting the executor down.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True

        pending = [future for future in list(self._worker_futures) if not future.done()]
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(asyncio.shield(future) for future in pending),
                                   return_exceptions=True),
                    timeout=max(0.0, float(grace_seconds)),
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Vieneu TTS shutdown grace expired with %d worker(s) still running",
                    sum(not future.done() for future in pending),
                )

        executor = getattr(self, "executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
