import unittest

from core.turn_metrics import TurnMetricsRecorder, config_fingerprint


class TurnMetricsTests(unittest.TestCase):
    def test_latest_summary_only_reports_first_binary_when_marked(self):
        recorder = TurnMetricsRecorder("session", {"api_key": "secret-a"})
        trace = recorder.start_turn(1, "chat")
        trace.finish("cancelled", llm_rounds=1, tool_calls=0)
        summary = recorder.latest_summary()

        self.assertEqual(summary["outcome"], "cancelled")
        self.assertEqual(summary["llm_rounds"], 1)
        self.assertNotIn("turn_start_to_first_ws_binary_ms", summary)

        trace = recorder.start_turn(2, "chat")
        trace.mark("first_ws_binary_sent")
        trace.finish("completed", llm_rounds=2, tool_calls=1)
        summary = recorder.latest_summary()
        self.assertGreaterEqual(summary["turn_start_to_first_ws_binary_ms"], 0)
        self.assertEqual(summary["llm_rounds"], 2)
        self.assertEqual(summary["tool_calls"], 1)

    def test_secret_values_do_not_change_config_fingerprint(self):
        left = config_fingerprint({"api_key": "one", "token": "a", "mode": "fast"})
        right = config_fingerprint({"api_key": "two", "token": "b", "mode": "fast"})
        self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
