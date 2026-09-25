import unittest
from unittest.mock import AsyncMock

from core.providers.asr.parakeet_silero import ParakeetSileroASR


class VADEndPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def _active_frame(self, *, end_threshold):
        asr = ParakeetSileroASR(
            vad_threshold=0.5,
            vad_threshold_low=0.3,
            vad_end_threshold=end_threshold,
            min_silence_duration_ms=450,
        )
        asr._speech_active = True
        asr._utterance_generation = 1
        asr._capture_generation = 1
        asr._voiced_ms = 320.0
        asr._silence_ms = 0.0
        asr._speech_probability = lambda _frame: 0.6
        asr._finish_utterance = AsyncMock()
        await asr._process_frame(b"\x00" * asr.FRAME_BYTES)
        return asr

    async def test_default_end_threshold_preserves_legacy_active_voice(self):
        asr = await self._active_frame(end_threshold=None)
        self.assertEqual(asr.vad_end_threshold, 0.3)
        self.assertEqual(asr._silence_ms, 0.0)
        self.assertGreater(asr._voiced_ms, 320.0)
        asr._finish_utterance.assert_not_awaited()

    async def test_high_end_threshold_can_count_tail_as_silence_independently(self):
        asr = await self._active_frame(end_threshold=0.8)
        self.assertEqual(asr.vad_threshold, 0.5)
        self.assertEqual(asr.vad_threshold_low, 0.3)
        self.assertEqual(asr.vad_end_threshold, 0.8)
        self.assertEqual(asr._voiced_ms, 320.0)
        self.assertAlmostEqual(asr._silence_ms, asr.FRAME_MS)
        asr._finish_utterance.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
