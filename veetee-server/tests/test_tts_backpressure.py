import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from core.providers.tts.scheduler import TTSPreempted
from core.providers.tts.vieneu_local import VieneuLocalTTS


class FastFakeEngine:
    def infer_stream(self, *args, **kwargs):
        for index in range(1000):
            yield index


class SingleChunkFakeEngine:
    def infer_stream(self, *args, **kwargs):
        yield 1


class EightChunkFakeEngine:
    def infer_stream(self, *args, **kwargs):
        for index in range(8):
            yield index


class FailingFakeEngine:
    def infer_stream(self, *args, **kwargs):
        raise RuntimeError("simulated vieneu failure")
        yield  # pragma: no cover


class BlockingFirstFakeEngine:
    def __init__(self):
        self.started = threading.Event()
        self.release_first = threading.Event()
        self.calls = 0

    def infer_stream(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            self.release_first.wait()
        yield 1


class StallingAfterFirstFakeEngine:
    def __init__(self):
        self.release_stall = threading.Event()

    def infer_stream(self, *args, **kwargs):
        yield 1
        self.release_stall.wait()
        yield 2


class FakeCodec:
    def resample_float32_48k_to_pcm16_24k(self, chunk):
        return chunk

    def chunk_pcm_to_opus_frames(self, chunk, remainder_buffer):
        return [b"frame"]

    def flush_remainder_to_opus_frame(self, remainder_buffer):
        return []


class PreemptibleFakeEngine:
    def __init__(self):
        self.dashboard_started = threading.Event()
        self.calls = []

    def infer_stream(self, text, *args, **kwargs):
        self.calls.append(text)
        if text == "background":
            self.dashboard_started.set()
            for _ in range(100):
                time.sleep(0.01)
                yield np.ones(480, dtype=np.float32) * 0.01
            return
        yield np.ones(480, dtype=np.float32) * 0.01


class PreemptionCodec(FakeCodec):
    def resample_float32_48k_to_pcm16_24k(self, chunk):
        return np.ones(1440, dtype=np.int16)


class TTSBackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_executor_and_is_idempotent(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._closed = False

        loop = asyncio.get_running_loop()
        future = tts._submit_worker(loop, lambda: "ok")
        self.assertEqual(await future, "ok")

        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)
        self.assertTrue(tts._closed)
        self.assertTrue(tts.executor._shutdown)
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            tts._submit_worker(loop, lambda: None)

    async def test_cancel_releases_worker_blocked_by_bounded_queue(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.codec = FakeCodec()
        tts.engine = FastFakeEngine()
        tts._engine_lock = threading.Lock()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()

        cancel_event = asyncio.Event()
        stream = tts.stream_sentence_to_opus("test", cancel_event)
        first_frame = await asyncio.wait_for(anext(stream), timeout=1.0)
        self.assertEqual(first_frame, b"frame")
        self.assertLessEqual(len(tts._worker_futures), 1)

        cancel_event.set()
        await asyncio.wait_for(stream.aclose(), timeout=1.0)

        deadline = asyncio.get_running_loop().time() + 1.0
        while tts._worker_futures and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        self.assertEqual(tts._worker_futures, set())
        tts.executor.shutdown(wait=True)

    async def test_external_cancel_drains_pending_queue_waiter(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.first_chunk_timeout_ms = 2000
        tts.stall_timeout_ms = 2000
        tts.codec = FakeCodec()
        engine = BlockingFirstFakeEngine()
        tts.engine = engine
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False

        before = set(asyncio.all_tasks())
        stream = tts.stream_sentence_to_opus("blocked", asyncio.Event())
        pull_task = asyncio.create_task(anext(stream))

        deadline = asyncio.get_running_loop().time() + 1.0
        while (
            not engine.started.is_set()
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)
        self.assertTrue(engine.started.is_set())
        await asyncio.sleep(0)

        queue_waiters = [
            task
            for task in asyncio.all_tasks()
            if task not in before
            and task is not pull_task
            and "Queue.get" in getattr(task.get_coro(), "__qualname__", "")
        ]
        self.assertEqual(len(queue_waiters), 1)
        self.assertFalse(queue_waiters[0].done())

        pull_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pull_task

        self.assertTrue(queue_waiters[0].done())
        engine.release_first.set()
        await asyncio.wait_for(stream.aclose(), timeout=1.0)
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_engine_lease_releases_before_buffered_audio_is_drained(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 8
        tts.first_chunk_timeout_ms = 500
        tts.stall_timeout_ms = 500
        tts.codec = FakeCodec()
        tts.engine = EightChunkFakeEngine()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False

        stream = tts.stream_sentence_to_opus("buffered", asyncio.Event())
        self.assertEqual(await asyncio.wait_for(anext(stream), timeout=0.5), b"frame")

        deadline = asyncio.get_running_loop().time() + 0.5
        while (
            tts.scheduler_snapshot()["active"]
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)

        self.assertFalse(tts.scheduler_snapshot()["active"])
        # Buffered playback is still available after the engine lease is free.
        self.assertEqual(await asyncio.wait_for(anext(stream), timeout=0.5), b"frame")

        await stream.aclose()
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_live_voice_preempts_dashboard_synthesis_at_chunk_boundary(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.source_voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.sample_rate = 24000
        tts.stream_queue_max_chunks = 2
        tts.first_chunk_timeout_ms = 500
        tts.stall_timeout_ms = 500
        tts.codec = PreemptionCodec()
        engine = PreemptibleFakeEngine()
        tts.engine = engine
        tts.executor = ThreadPoolExecutor(max_workers=2)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False

        background = asyncio.create_task(
            tts.synthesize_wav("background", priority="dashboard")
        )
        deadline = asyncio.get_running_loop().time() + 1.0
        while (
            not engine.dashboard_started.is_set()
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)
        self.assertTrue(engine.dashboard_started.is_set())

        started = asyncio.get_running_loop().time()
        frames = []
        async for frame in tts.stream_sentence_to_opus(
            "live", asyncio.Event(), priority="live"
        ):
            frames.append(frame)
        live_wait_ms = (asyncio.get_running_loop().time() - started) * 1000.0

        self.assertEqual(frames, [b"frame"])
        self.assertLess(live_wait_ms, 500.0)
        with self.assertRaises(TTSPreempted):
            await background
        snapshot = tts.scheduler_snapshot()
        self.assertEqual(snapshot["preemption_requests"], 1)
        self.assertFalse(snapshot["active"])

        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_worker_exception_is_propagated_to_stream_consumer(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.codec = FakeCodec()
        tts.engine = FailingFakeEngine()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()

        with self.assertRaisesRegex(RuntimeError, "Vieneu worker failed"):
            async for _ in tts.stream_sentence_to_opus("test", asyncio.Event()):
                pass

        tts.executor.shutdown(wait=True)

    async def test_live_first_chunk_budget_includes_scheduler_wait(self):
        from core.providers.tts.scheduler import TTSAdmissionScheduler

        scheduler = TTSAdmissionScheduler()
        holder = await scheduler.acquire("live")

        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.admission_timeout_ms = 80
        tts.first_chunk_timeout_ms = 80
        tts.stall_timeout_ms = 100
        tts.codec = FakeCodec()
        tts.engine = FastFakeEngine()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False
        tts._scheduler = scheduler

        started = asyncio.get_running_loop().time()
        stream = tts.stream_sentence_to_opus(
            "queued-live",
            asyncio.Event(),
            priority="live_first",
            queue_deadline_seconds=10.0,
        )
        with self.assertRaisesRegex(
            TimeoutError,
            "admission timeout while waiting for engine",
        ):
            await asyncio.wait_for(anext(stream), timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.3)
        self.assertEqual(scheduler.snapshot()["waiting"]["live_first"], 0)
        self.assertTrue(scheduler.snapshot()["active"])

        await holder.release()
        tts.executor.shutdown(wait=True)

    async def test_initial_turn_scheduler_wait_has_separate_admission_budget(self):
        from core.providers.tts.scheduler import TTSAdmissionScheduler

        scheduler = TTSAdmissionScheduler()
        holder = await scheduler.acquire("live")

        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.admission_timeout_ms = 500
        tts.first_chunk_timeout_ms = 100
        tts.stall_timeout_ms = 100
        tts.codec = FakeCodec()
        tts.engine = SingleChunkFakeEngine()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False
        tts._scheduler = scheduler

        async def release_holder():
            await asyncio.sleep(0.16)
            await holder.release()

        release_task = asyncio.create_task(release_holder())
        started = asyncio.get_running_loop().time()
        stream = tts.stream_sentence_to_opus(
            "initial-turn",
            asyncio.Event(),
            priority="live_first",
            queue_deadline_seconds=1.0,
            initial_turn_audio=True,
        )
        frame = await asyncio.wait_for(anext(stream), timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(frame, b"frame")
        self.assertGreater(elapsed, 0.1)
        self.assertLess(elapsed, 0.4)
        await release_task
        await stream.aclose()
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_later_turn_segment_may_wait_for_live_engine_then_keeps_pcm_deadline(self):
        from core.providers.tts.scheduler import TTSAdmissionScheduler

        scheduler = TTSAdmissionScheduler()
        holder = await scheduler.acquire("live")

        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.first_chunk_timeout_ms = 100
        tts.stall_timeout_ms = 100
        tts.codec = FakeCodec()
        tts.engine = SingleChunkFakeEngine()
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._detached_cleanup_tasks = set()
        tts._quarantined_worker = None
        tts._closed = False
        tts._scheduler = scheduler

        async def release_holder():
            await asyncio.sleep(0.16)
            await holder.release()

        release_task = asyncio.create_task(release_holder())
        started = asyncio.get_running_loop().time()
        stream = tts.stream_sentence_to_opus(
            "later-segment",
            asyncio.Event(),
            priority="live",
            queue_deadline_seconds=0.5,
            initial_turn_audio=False,
        )
        frame = await asyncio.wait_for(anext(stream), timeout=0.5)
        elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(frame, b"frame")
        self.assertGreater(elapsed, 0.1)
        await release_task
        await stream.aclose()
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_first_chunk_timeout_is_enforced_and_engine_is_quarantined(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.first_chunk_timeout_ms = 100
        tts.stall_timeout_ms = 100
        tts.codec = FakeCodec()
        engine = BlockingFirstFakeEngine()
        tts.engine = engine
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._closed = False

        stream = tts.stream_sentence_to_opus("first", asyncio.Event())
        with self.assertRaisesRegex(TimeoutError, "first chunk timeout"):
            await asyncio.wait_for(anext(stream), timeout=0.5)
        self.assertTrue(tts.scheduler_snapshot()["active"])

        engine.release_first.set()
        deadline = asyncio.get_running_loop().time() + 1.0
        while tts.scheduler_snapshot()["active"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(tts.scheduler_snapshot()["active"])
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_stream_stall_timeout_is_enforced_after_first_chunk(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.first_chunk_timeout_ms = 300
        tts.stall_timeout_ms = 100
        tts.codec = FakeCodec()
        engine = StallingAfterFirstFakeEngine()
        tts.engine = engine
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._closed = False

        stream = tts.stream_sentence_to_opus("test", asyncio.Event())
        self.assertEqual(await asyncio.wait_for(anext(stream), timeout=0.3), b"frame")
        with self.assertRaisesRegex(TimeoutError, "stream stall timeout"):
            await asyncio.wait_for(anext(stream), timeout=0.5)

        engine.release_stall.set()
        deadline = asyncio.get_running_loop().time() + 1.0
        while tts.scheduler_snapshot()["active"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(tts.scheduler_snapshot()["active"])
        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)

    async def test_cancel_does_not_wait_forever_for_stuck_native_worker(self):
        tts = object.__new__(VieneuLocalTTS)
        tts.voice = "test"
        tts.denoise = False
        tts.temperature = 0.0
        tts.stream_queue_max_chunks = 1
        tts.codec = FakeCodec()
        engine = BlockingFirstFakeEngine()
        tts.engine = engine
        tts.executor = ThreadPoolExecutor(max_workers=1)
        tts._worker_futures = set()
        tts._closed = False

        cancel_event = asyncio.Event()
        stream = tts.stream_sentence_to_opus("first", cancel_event)
        next_task = asyncio.create_task(anext(stream))

        deadline = asyncio.get_running_loop().time() + 1.0
        while not engine.started.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertTrue(engine.started.is_set())

        cancel_event.set()
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(next_task, timeout=0.5)

        self.assertTrue(tts.scheduler_snapshot()["active"])
        with self.assertRaisesRegex(RuntimeError, "still stopping"):
            await anext(tts.stream_sentence_to_opus("second", asyncio.Event()))
        self.assertEqual(engine.calls, 1)

        engine.release_first.set()
        deadline = asyncio.get_running_loop().time() + 1.0
        while tts.scheduler_snapshot()["active"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        self.assertFalse(tts.scheduler_snapshot()["active"])

        recovered = tts.stream_sentence_to_opus("third", asyncio.Event())
        self.assertEqual(await asyncio.wait_for(anext(recovered), timeout=0.5), b"frame")
        await recovered.aclose()
        self.assertEqual(engine.calls, 2)

        await asyncio.wait_for(tts.shutdown(grace_seconds=0.5), timeout=1.0)


if __name__ == "__main__":
    unittest.main()
