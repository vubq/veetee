import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.providers.asr.parakeet_silero import ParakeetSileroASR, _QueuedUtterance


class ASRDiagnosticCaptureTests(unittest.TestCase):
    def test_capture_writes_metadata_and_prunes_old_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            asr = ParakeetSileroASR(
                diagnostic_capture_enabled=True,
                diagnostic_capture_dir=temp_dir,
                diagnostic_capture_max_files=2,
            )

            for generation in range(3):
                queued = _QueuedUtterance(
                    generation=generation,
                    pcm=np.zeros(1600, dtype=np.float32),
                    enqueued_perf=0.0,
                    audio_ms=100.0,
                    endpoint_reason="client_finalize",
                    trailing_silence_ms=96.0,
                    voiced_ms=320.0,
                )
                asr._write_diagnostic_capture_sync(
                    queued,
                    f"câu {generation}",
                    0.91,
                )

            capture_dir = Path(temp_dir)
            wav_files = sorted(capture_dir.glob("asr_*.wav"))
            json_files = sorted(capture_dir.glob("asr_*.json"))
            self.assertEqual(len(wav_files), 2)
            self.assertEqual(len(json_files), 2)

            metadata = json.loads(json_files[-1].read_text(encoding="utf-8"))
            self.assertEqual(metadata["transcript"], "câu 2")
            self.assertEqual(metadata["endpoint_reason"], "client_finalize")
            self.assertEqual(metadata["trailing_silence_ms"], 96.0)
            self.assertEqual(metadata["voiced_ms"], 320.0)
            self.assertEqual(metadata["min_word_confidence"], 0.91)


class ASRAudioIdleEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_idle_finalizes_after_observed_trailing_silence(self):
        asr = ParakeetSileroASR(min_silence_duration_ms=320)
        asr._running = True
        asr._speech_active = True
        asr._capture_generation = 7
        asr._utterance_generation = 7
        asr._silence_ms = 96.0
        asr._speech_buffer.extend(b"\x01\x00" * 512)
        asr._pcm_pending.extend(b"\x00\x00" * 80)

        calls = []

        async def fake_finish(*, endpoint_reason="unknown"):
            calls.append(endpoint_reason)
            asr._reset_utterance_state()

        asr._finish_utterance = fake_finish

        await asr._finalize_after_audio_idle(0.0, 7)

        self.assertEqual(calls, ["audio_idle"])
        self.assertFalse(asr._speech_active)
        self.assertEqual(asr._pcm_pending, bytearray())

    async def test_audio_idle_does_not_finalize_without_observed_silence(self):
        asr = ParakeetSileroASR(min_silence_duration_ms=320)
        asr._running = True
        asr._speech_active = True
        asr._capture_generation = 3
        asr._utterance_generation = 3
        asr._silence_ms = 0.0

        called = False

        async def fake_finish(*, endpoint_reason="unknown"):
            nonlocal called
            called = True

        asr._finish_utterance = fake_finish

        await asr._finalize_after_audio_idle(0.0, 3)

        self.assertFalse(called)
        self.assertTrue(asr._speech_active)


if __name__ == "__main__":
    unittest.main()
