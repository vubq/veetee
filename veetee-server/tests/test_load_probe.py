import unittest

from scripts.load_probe import reconcile_level_results, summarize_server_metrics


class LoadProbeReconciliationTests(unittest.TestCase):
    def test_server_outcome_is_authoritative_over_recovery_protocol_completion(self):
        results = [
            {
                "turn_records": [
                    {
                        "turn_id": f"s{session}:{turn}",
                        "protocol_completed": True,
                        "latency_s": float(turn),
                        "binaries": 5,
                    }
                    for turn in range(1, 4)
                ]
            }
            for session in range(1, 5)
        ]
        server_metrics = {
            "observed_turns": 12,
            "completed_turns": 2,
            "outcomes": {"completed": 2, "failed": 10},
            "_observed_turn_ids": [
                f"s{session}:{turn}"
                for session in range(1, 5)
                for turn in range(1, 4)
            ],
            "_completed_turn_ids": ["s1:1", "s2:1"],
        }

        result = reconcile_level_results(results, server_metrics, 12)

        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["failed"], 10)
        self.assertAlmostEqual(result["success_rate"], 2 / 12)
        self.assertEqual(result["protocol_completed"], 12)
        self.assertEqual(result["protocol_failed"], 0)
        self.assertEqual(result["protocol_success_rate"], 1.0)
        self.assertEqual(result["observed_turns"], 12)
        self.assertEqual(result["unobserved_turns"], 0)
        self.assertTrue(result["trace_complete"])
        self.assertEqual(result["p50_s"], 1.0)
        self.assertEqual(result["max_s"], 1.0)
        self.assertNotIn("_completed_turn_ids", server_metrics)
        self.assertNotIn("_observed_turn_ids", server_metrics)

    def test_unobserved_turns_fail_closed(self):
        results = [{
            "turn_records": [
                {"turn_id": "s:1", "protocol_completed": True, "latency_s": 1.0},
                {"turn_id": "s:2", "protocol_completed": True, "latency_s": 2.0},
            ]
        }]
        server_metrics = {
            "observed_turns": 1,
            "completed_turns": 1,
            "outcomes": {"completed": 1},
            "_observed_turn_ids": ["s:1"],
            "_completed_turn_ids": ["s:1"],
        }

        result = reconcile_level_results(results, server_metrics, 2)

        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["protocol_completed"], 2)
        self.assertEqual(result["unobserved_turns"], 1)
        self.assertFalse(result["trace_complete"])

    def test_server_metric_summary_keeps_completed_latency_only(self):
        diagnostics = {
            "runtime": {
                "latest_turns": [
                    {
                        "session_id": "s",
                        "turn_id": "s:1",
                        "outcome": "completed",
                        "turn_start_to_first_ws_binary_ms": 500,
                        "latency_ms": {
                            "llm_headers": 300,
                            "tts_queue_to_lock": 10,
                            "tts_first_pcm": 100,
                            "tts_lease_held": 800,
                        },
                        "stage_aggregates_ms": {
                            "tts_queue_wait": {
                                "count": 3,
                                "total_ms": 40,
                                "max_ms": 25,
                                "p50_ms": 10,
                            },
                            "tts_lease_held": {
                                "count": 3,
                                "total_ms": 2400,
                                "max_ms": 1200,
                                "p50_ms": 800,
                            },
                            "tts_inference": {
                                "count": 3,
                                "total_ms": 1800,
                                "max_ms": 900,
                                "p50_ms": 600,
                            },
                        },
                    },
                    {
                        "session_id": "s",
                        "turn_id": "s:2",
                        "outcome": "failed",
                        "turn_start_to_first_ws_binary_ms": 50,
                        "latency_ms": {
                            "llm_headers": 20,
                            "tts_queue_to_lock": 1,
                            "tts_first_pcm": 2,
                        },
                    },
                ]
            }
        }

        result = summarize_server_metrics(diagnostics, {"s"})

        self.assertEqual(result["observed_turns"], 2)
        self.assertEqual(result["completed_turns"], 1)
        self.assertEqual(result["outcomes"], {"completed": 1, "failed": 1})
        self.assertEqual(result["turn_start_to_first_ws_binary_ms"]["p50_ms"], 500.0)
        self.assertEqual(result["llm_headers"]["p50_ms"], 300.0)
        self.assertEqual(result["tts_lease_held"]["p50_ms"], 800.0)
        self.assertEqual(result["tts_queue_wait_max"]["p50_ms"], 25.0)
        self.assertEqual(result["tts_queue_wait_total"]["p50_ms"], 40.0)
        self.assertEqual(result["tts_lease_held_max"]["p50_ms"], 1200.0)
        self.assertEqual(result["tts_lease_held_total"]["p50_ms"], 2400.0)
        self.assertEqual(result["tts_inference_max"]["p50_ms"], 900.0)
        self.assertEqual(result["tts_inference_total"]["p50_ms"], 1800.0)
        self.assertEqual(result["tts_segment_count"]["p50"], 3.0)
        self.assertEqual(result["_completed_turn_ids"], ["s:1"])


if __name__ == "__main__":
    unittest.main()
