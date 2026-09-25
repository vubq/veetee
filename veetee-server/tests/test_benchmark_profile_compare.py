import unittest

from scripts.compare_benchmark_profiles import compare_summaries


class BenchmarkProfileComparisonTests(unittest.TestCase):
    def test_reports_descriptive_deltas_without_selecting_profile(self):
        baseline = {
            "requested_samples": 100,
            "successful_samples": 99,
            "sla_status": "PARTIAL",
            "p50_ms": 620.0,
            "p90_ms": 900.0,
            "p95_ms": 980.0,
            "max_ms": 1200.0,
            "success_rate": 0.99,
            "stage_latency_ms": {
                "speech_end_to_stt_final_ms": {
                    "samples": 99, "p50": 320.0, "p95": 400.0, "max": 500.0
                },
            },
        }
        candidate = {
            "requested_samples": 100,
            "successful_samples": 100,
            "sla_status": "ACHIEVED",
            "p50_ms": 560.0,
            "p90_ms": 800.0,
            "p95_ms": 890.0,
            "max_ms": 1100.0,
            "success_rate": 1.0,
            "stage_latency_ms": {
                "speech_end_to_stt_final_ms": {
                    "samples": 100, "p50": 260.0, "p95": 330.0, "max": 430.0
                },
            },
        }

        result = compare_summaries(baseline, [("endpoint-320", candidate)])

        row = result["comparisons"][0]
        self.assertEqual(row["p50_ms_delta"], -60.0)
        self.assertEqual(row["p95_ms_delta"], -90.0)
        self.assertEqual(
            row["stage_latency_ms"]["speech_end_to_stt_final_ms"]["p95_delta"],
            -70.0,
        )
        self.assertNotIn("winner", result)
        self.assertIn("CER/WER", result["decision_note"])


if __name__ == "__main__":
    unittest.main()
