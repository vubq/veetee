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


if __name__ == "__main__":
    unittest.main()
