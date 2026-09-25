import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np

from core.providers.asr.parakeet_silero import (
    ParakeetSileroASR,
    _ParakeetRuntime,
    _QueuedUtterance,
)


class SpeculativeASRTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_after_trailing_silence_threshold(self):
        asr = ParakeetSileroASR(
            min_silence_duration_ms=320,
            min_speech_duration_ms=96,
            speculative_inference_enabled=True,
            speculative_start_silence_ms=64,
            speculative_min_confidence=0.95,
        )
        asr._speech_active = True
        asr._capture_generation = 7
        asr._utterance_generation = 7
        asr._voiced_ms = 320.0
        asr._silence_ms = 64.0
        asr._speech_buffer.extend((np.ones(3200, dtype=np.int16) * 512).tobytes())

        with patch.object(
            _ParakeetRuntime,
            "transcribe_if_current",
            new=AsyncMock(return_value=("xin chào", 0.99, False)),
        ):
            asr._maybe_start_speculative_inference()
            self.assertIsNotNone(asr._speculative_task)
            self.assertTrue(asr._speculative_valid)
            result, _elapsed_ms = await asr._speculative_task

        self.assertEqual(result[0], "xin chào")
        self.assertEqual(result[1], 0.99)

    async def test_low_confidence_preview_is_emitted_without_lowering_final_reuse_gate(self):
        preview = AsyncMock()
        asr = ParakeetSileroASR(
            speculative_inference_enabled=True,
            speculative_min_confidence=0.95,
            on_speculative_transcript_callback=preview,
        )
        asr._capture_generation = 8
        asr._utterance_generation = 8
        task = asyncio.create_task(
            asyncio.sleep(0, result=(("hôm nay thứ mấy", 0.84, False), 52.0))
        )
        asr._speculative_task = task
        asr._speculative_valid = True
        asr._speculative_tasks.add(task)
        await task

        asr._on_speculative_done(task)
        await asyncio.sleep(0)

        preview.assert_awaited_once_with("hôm nay thứ mấy", 0.84, 8)
        # The same 0.84 snapshot is still below the 0.95 final-ASR reuse gate;
        # the transcription worker test below verifies it triggers final inference.
        self.assertEqual(asr.speculative_min_confidence, 0.95)

    async def test_speech_resume_invalidates_snapshot_without_blocking_audio(self):
        asr = ParakeetSileroASR(
            vad_threshold=0.5,
            vad_threshold_low=0.3,
            vad_end_threshold=0.3,
            min_silence_duration_ms=320,
            speculative_inference_enabled=True,
            speculative_start_silence_ms=64,
        )
        asr._speech_active = True
        asr._capture_generation = 1
        asr._utterance_generation = 1
        asr._voiced_ms = 320.0
        asr._silence_ms = 96.0
        asr._speculative_valid = True
        asr._speculative_task = asyncio.create_task(asyncio.sleep(10))
        task = asr._speculative_task
        asr._speculative_tasks.add(task)
        asr._speech_probability = lambda _frame: 0.9

        try:
            await asr._process_frame(b"\x00" * asr.FRAME_BYTES)
            self.assertFalse(asr._speculative_valid)
            self.assertEqual(asr._silence_ms, 0.0)
            self.assertGreater(asr._voiced_ms, 320.0)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_high_confidence_speculative_result_skips_second_inference(self):
        callback = AsyncMock()
        asr = ParakeetSileroASR(
            on_transcript_callback=callback,
            speculative_inference_enabled=True,
            speculative_min_confidence=0.95,
        )
        asr._capture_generation = 3
        speculative_task = asyncio.create_task(
            asyncio.sleep(0, result=(("mấy giờ rồi", 0.99, False), 87.0))
        )
        queued = _QueuedUtterance(
            generation=3,
            pcm=np.zeros(1600, dtype=np.float32),
            enqueued_perf=0.0,
            audio_ms=100.0,
            endpoint_reason="silence",
            trailing_silence_ms=320.0,
            voiced_ms=500.0,
            speculative_task=speculative_task,
            speculative_valid=True,
        )
        await asr._utterance_queue.put(queued)
        await asr._utterance_queue.put(None)

        with patch.object(
            _ParakeetRuntime,
            "transcribe_if_current",
            new=AsyncMock(side_effect=AssertionError("final inference should not run")),
        ):
            await asr._transcription_worker()

        callback.assert_awaited_once_with("mấy giờ rồi", True, True, 3)
        self.assertEqual(asr.last_word_confidence, 0.99)

    async def test_low_confidence_snapshot_falls_back_to_final_inference(self):
        callback = AsyncMock()
        asr = ParakeetSileroASR(
            on_transcript_callback=callback,
            speculative_inference_enabled=True,
            speculative_min_confidence=0.95,
        )
        asr._capture_generation = 4
        speculative_task = asyncio.create_task(
            asyncio.sleep(0, result=(("không chắc", 0.70, False), 70.0))
        )
        queued = _QueuedUtterance(
            generation=4,
            pcm=np.zeros(1600, dtype=np.float32),
            enqueued_perf=0.0,
            audio_ms=100.0,
            endpoint_reason="silence",
            trailing_silence_ms=320.0,
            voiced_ms=500.0,
            speculative_task=speculative_task,
            speculative_valid=True,
        )
        await asr._utterance_queue.put(queued)
        await asr._utterance_queue.put(None)

        final = AsyncMock(return_value=("mấy giờ rồi", 0.98, False))
        with patch.object(
            _ParakeetRuntime,
            "transcribe_if_current",
            new=final,
        ):
            await asr._transcription_worker()

        final.assert_awaited_once()
        callback.assert_awaited_once_with("mấy giờ rồi", True, True, 4)
        self.assertEqual(asr.last_word_confidence, 0.98)


if __name__ == "__main__":
    unittest.main()
