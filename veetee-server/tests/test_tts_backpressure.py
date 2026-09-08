import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from core.providers.tts.vieneu_local import VieneuLocalTTS


class FastFakeEngine:
    def infer_stream(self, *args, **kwargs):
        for index in range(1000):
            yield index


class FakeCodec:
    def resample_float32_48k_to_pcm16_24k(self, chunk):
        return chunk

    def chunk_pcm_to_opus_frames(self, chunk, remainder_buffer):
        return [b"frame"]

    def flush_remainder_to_opus_frame(self, remainder_buffer):
        return []


class TTSBackpressureTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
