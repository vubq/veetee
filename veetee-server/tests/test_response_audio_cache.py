import asyncio
import unittest
from types import SimpleNamespace

from config.settings import TTSConfig
from core.response_audio_cache import MAX_CACHE_ENTRIES, ResponseAudioCache
from server import VeeTeeServer


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

    async def test_recovery_asset_requires_ai_provenance_and_is_cached_only(self):
        cache, tts = self.make_cache()
        prepared = await cache.prepare_recovery(
            "Mình đang gặp sự cố, bạn thử lại nhé.",
            1,
            provenance="ai:test-model",
        )
        recovered = await cache.get_recovery()

        self.assertEqual(recovered.text, prepared.text)
        self.assertEqual(recovered.frames, prepared.frames)
        self.assertEqual(recovered.provenance, "ai:test-model")
        self.assertEqual(tts.calls, 1)

    async def test_recovery_timeout_reuses_one_pending_fill(self):
        cache, tts = self.make_cache()
        tts.release = asyncio.Event()

        with self.assertRaises(asyncio.TimeoutError):
            await cache.prepare_recovery(
                "Bạn thử lại nhé.",
                0.01,
                provenance="ai:test-model",
            )

        waiter = asyncio.create_task(cache.prepare_recovery(
            "Một câu khác không được tạo job mới.",
            1.0,
            provenance="ai:test-model",
        ))
        await asyncio.sleep(0)
        tts.release.set()
        prepared = await asyncio.wait_for(waiter, timeout=1.0)

        self.assertEqual(prepared.text, "Bạn thử lại nhé.")
        self.assertEqual(tts.calls, 1)
        self.assertIsNone(await cache.get_pending_recovery())

    async def test_late_recovery_fill_is_promoted_without_new_tts_call(self):
        cache, tts = self.make_cache()
        tts.release = asyncio.Event()
        text = "Bạn thử lại nhé."

        with self.assertRaises(asyncio.TimeoutError):
            await cache.prepare_recovery(
                text,
                0.01,
                provenance="ai:test-model",
            )

        tts.release.set()
        await cache.get_or_fill(text, 1.0, priority="prewarm")
        recovered = await cache.get_recovery()

        self.assertEqual(recovered.text, text)
        self.assertEqual(recovered.provenance, "ai:test-model")
        self.assertEqual(tts.calls, 1)

    async def test_server_error_fallback_prewarm_uses_ai_generated_text(self):
        class RecoveryLLM:
            calls = 0

            async def generate_recovery_message(self):
                self.calls += 1
                return "Mình đang gặp sự cố, bạn thử lại nhé."

        cache, tts = self.make_cache()
        server = object.__new__(VeeTeeServer)
        server.response_audio_cache = cache
        server.llm_engine = RecoveryLLM()
        server.config = SimpleNamespace(
            conversation=SimpleNamespace(
                fixed_response_timeout_seconds=1.0,
                ai_control_timeout_ms=500,
            ),
            llm=SimpleNamespace(model="test-model"),
        )
        server.runtime_readiness = {
            "error_fallback_ready": False,
            "error_fallback_provenance": "",
        }

        ready = await server._prewarm_error_fallback()
        cached = await cache.get_recovery()

        self.assertTrue(ready)
        self.assertTrue(server.runtime_readiness["error_fallback_ready"])
        self.assertEqual(server.runtime_readiness["error_fallback_provenance"], "ai:test-model")
        self.assertEqual(cached.text, "Mình đang gặp sự cố, bạn thử lại nhé.")
        self.assertEqual(cached.provenance, "ai:test-model")
        self.assertEqual(server.llm_engine.calls, 1)
        self.assertEqual(tts.calls, 1)

    async def test_server_retries_pending_recovery_without_regenerating_text(self):
        class RecoveryLLM:
            calls = 0

            async def generate_recovery_message(self):
                self.calls += 1
                return "Bạn thử lại nhé."

        cache, tts = self.make_cache()
        tts.release = asyncio.Event()
        server = object.__new__(VeeTeeServer)
        server.response_audio_cache = cache
        server.llm_engine = RecoveryLLM()
        server.config = SimpleNamespace(
            conversation=SimpleNamespace(
                fixed_response_timeout_seconds=0.01,
                ai_control_timeout_ms=500,
            ),
            llm=SimpleNamespace(model="test-model"),
        )
        server.runtime_readiness = {
            "error_fallback_ready": False,
            "error_fallback_provenance": "",
        }

        self.assertFalse(await server._prewarm_error_fallback())
        self.assertFalse(await server._prewarm_error_fallback())
        self.assertEqual(server.llm_engine.calls, 1)
        self.assertEqual(tts.calls, 1)

        tts.release.set()
        self.assertTrue(await server._prewarm_error_fallback())
        self.assertEqual(server.llm_engine.calls, 1)
        self.assertEqual(tts.calls, 1)


if __name__ == "__main__":
    unittest.main()
