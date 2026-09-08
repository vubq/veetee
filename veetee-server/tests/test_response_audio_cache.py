import asyncio
import unittest

from config.settings import TTSConfig
from core.response_audio_cache import MAX_CACHE_ENTRIES, ResponseAudioCache


class CountingTTS:
    def __init__(self):
        self.calls = 0
        self.voice = "voice-a"
        self.source_voice = "source-a"
        self.sample_rate = 24000
        self.frame_duration_ms = 60
        self.denoise = True
        self.temperature = 0.7
        self.started = asyncio.Event()
        self.release = None
        self.fail = False

    async def stream_sentence_to_opus(self, text, cancel_event=None):
        self.calls += 1
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("synthetic TTS failure")
        yield f"{text}:1".encode()
        yield f"{text}:2".encode()


class ResponseAudioCacheTests(unittest.IsolatedAsyncioTestCase):
    def make_cache(self, tts=None):
        engine = tts or CountingTTS()
        config = TTSConfig(voice="voice-a", source_voice="source-a")
        return ResponseAudioCache(engine, config), engine

    async def test_hit_does_not_call_tts_again(self):
        cache, tts = self.make_cache()
        first = await cache.get_or_fill("hello", 1)
        second = await cache.get_or_fill("hello", 1)
        self.assertFalse(first.hit)
        self.assertTrue(second.hit)
        self.assertEqual(first.frames, second.frames)
        self.assertEqual(tts.calls, 1)

    async def test_two_waiters_share_one_fill_and_cancelling_one_does_not_cancel_fill(self):
        cache, tts = self.make_cache()
        tts.release = asyncio.Event()
        waiter_a = asyncio.create_task(cache.get_or_fill("hello", 1))
        await asyncio.wait_for(tts.started.wait(), timeout=1)
        waiter_b = asyncio.create_task(cache.get_or_fill("hello", 1))
        waiter_a.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_a
        tts.release.set()
        result_b = await asyncio.wait_for(waiter_b, timeout=1)
        self.assertEqual(len(result_b.frames), 2)
        self.assertEqual(tts.calls, 1)

    async def test_error_does_not_poison_key(self):
        cache, tts = self.make_cache()
        tts.fail = True
        with self.assertRaises(RuntimeError):
            await cache.get_or_fill("hello", 1)
        tts.fail = False
        result = await cache.get_or_fill("hello", 1)
        self.assertEqual(len(result.frames), 2)
        self.assertEqual(tts.calls, 2)

    async def test_voice_change_causes_miss(self):
        cache, tts = self.make_cache()
        await cache.get_or_fill("hello", 1)
        tts.voice = "voice-b"
        await cache.get_or_fill("hello", 1)
        self.assertEqual(tts.calls, 2)

    async def test_lru_entry_count_is_bounded(self):
        cache, _ = self.make_cache()
        for index in range(MAX_CACHE_ENTRIES + 3):
            await cache.get_or_fill(f"text-{index}", 1)
        self.assertLessEqual(len(cache._entries), MAX_CACHE_ENTRIES)

    async def test_shutdown_cancels_inflight_fill(self):
        cache, tts = self.make_cache()
        tts.release = asyncio.Event()
        waiter = asyncio.create_task(cache.get_or_fill("hello", 10))
        await asyncio.wait_for(tts.started.wait(), timeout=1)
        await cache.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await waiter


if __name__ == "__main__":
    unittest.main()
