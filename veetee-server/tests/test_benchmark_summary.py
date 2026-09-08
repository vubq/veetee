import unittest
import struct

from scripts.benchmark_pipeline import _first_voiced_sample_offset, summarize


class BenchmarkSummaryTests(unittest.TestCase):
    @staticmethod
    def record(outcome, latency=None):
        return {
            "measured": True,
            "outcome": outcome,
            "speech_end_to_first_voiced_pcm_received_ms": latency,
        }

    def test_sla_fails_when_only_one_of_one_hundred_attempts_succeeds(self):
        records = [self.record("completed", 500.0)]
        records.extend(self.record("timeout") for _ in range(99))
        summary = summarize(records)
        self.assertEqual(summary["requested_samples"], 100)
        self.assertEqual(summary["successful_samples"], 1)
        self.assertEqual(summary["timeout_samples"], 99)
        self.assertFalse(summary["sla_p95_lt_1000_ms"])

    def test_sla_fails_when_all_attempts_timeout(self):
        summary = summarize([self.record("timeout") for _ in range(100)])
        self.assertEqual(summary["successful_samples"], 0)
        self.assertFalse(summary["sla_p95_lt_1000_ms"])

    def test_first_voiced_pcm_ignores_leading_silence_windows(self):
        silence = [0] * 320
        speech = [1000] * 160
        pcm = b"".join(struct.pack("<h", value) for value in silence + speech)

        offset, rms = _first_voiced_sample_offset(
            pcm,
            sample_rate=16000,
            rms_threshold=160.0,
        )

        self.assertEqual(offset, 320)
        self.assertEqual(rms, 1000.0)


if __name__ == "__main__":
    unittest.main()
