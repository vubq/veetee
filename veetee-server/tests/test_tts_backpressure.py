import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.providers.tts.vieneu_local import VieneuLocalTTS


class FastFakeEngine:
    def infer_stream(self, *args, **kwargs):
        for index in range(1000):
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


class FakeCodec:
    def resample_float32_48k_to_pcm16_24k(self, chunk):
        return chunk

    def chunk_pcm_to_opus_frames(self, chunk, remainder_buffer):
        return [b"frame"]

    def flush_remainder_to_opus_frame(self, remainder_buffer):
        return []


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
