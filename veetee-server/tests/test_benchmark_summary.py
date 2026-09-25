import unittest
import struct

from scripts.benchmark_pipeline import RunTrace, _first_voiced_sample_offset, summarize
from scripts.load_probe import percentile, summarize_server_metrics


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

    def test_summary_reports_stage_percentiles_without_summing_them(self):
        records = []
        for value in (100.0, 200.0, 300.0):
            record = self.record("completed", value + 180.0)
            record.update({
                "speech_end_to_stt_final_ms": value,
                "stt_final_to_first_clause_ms": 50.0,
                "first_clause_to_first_binary_received_ms": 20.0,
                "first_binary_to_first_voiced_pcm_received_ms": 10.0,
                "speech_end_to_first_binary_received_ms": value + 70.0,
            })
            records.append(record)

        summary = summarize(records)
        stages = summary["stage_latency_ms"]
        self.assertEqual(stages["speech_end_to_stt_final_ms"]["p50"], 200.0)
        self.assertEqual(stages["stt_final_to_first_clause_ms"]["p95"], 50.0)
        self.assertEqual(stages["speech_end_to_first_voiced_pcm_received_ms"]["samples"], 3)

    def test_audio_continuity_tracks_frame_gap_percentiles_and_stalls(self):
        trace = RunTrace(run_index=0, measured=True, mode="auto")
        for at in (10.0, 10.06, 10.12, 10.30):
            trace.observe_audio_frame(at, expected_frame_ms=60.0)
        trace.outcome = "completed"
        trace.marks["speech_end"] = 9.0
        trace.marks["first_voiced_pcm_received"] = 10.0

        record = trace.record()
        self.assertEqual(record["audio_frame_gap_max_ms"], 180.0)
        self.assertEqual(record["audio_gap_over_2x_frame_count"], 1)

        summary = summarize([record])
        continuity = summary["audio_continuity"]
        self.assertEqual(continuity["samples"], 1)
        self.assertEqual(continuity["run_gap_max_ms"], 180.0)
        self.assertEqual(continuity["gap_over_2x_frame_count"], 1)

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


class LoadProbePercentileTests(unittest.TestCase):
    def test_percentile_basic(self):
        values = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(percentile(values, 50), 3.0)
        self.assertEqual(percentile(values, 95), 4.0)

    def test_percentile_empty(self):
        self.assertIsNone(percentile([], 95))

    def test_load_probe_server_metrics_are_scoped_to_probe_sessions(self):
        diagnostics = {
            "runtime": {
                "latest_turns": [
                    {
                        "session_id": "probe-a",
                        "outcome": "completed",
                        "turn_start_to_first_ws_binary_ms": 500.0,
                        "latency_ms": {
                            "llm_headers": 350.0,
                            "llm_first_speech_segment": 370.0,
                            "tts_queue_to_lock": 5.0,
                            "tts_first_pcm": 110.0,
                        },
                    },
                    {
                        "session_id": "other",
                        "outcome": "completed",
                        "turn_start_to_first_ws_binary_ms": 9999.0,
                        "latency_ms": {"tts_queue_to_lock": 9999.0},
                    },
                ]
            }
        }

        result = summarize_server_metrics(diagnostics, {"probe-a"})

        self.assertEqual(result["observed_turns"], 1)
        self.assertEqual(result["completed_turns"], 1)
        self.assertEqual(result["outcomes"], {"completed": 1})
        self.assertEqual(
            result["turn_start_to_first_ws_binary_ms"]["p50_ms"], 500.0
        )
        self.assertEqual(result["tts_queue_to_lock"]["p50_ms"], 5.0)


if __name__ == "__main__":
    unittest.main()
