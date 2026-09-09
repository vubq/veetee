import unittest

from core.turn_metrics import TurnMetricsRecorder, TurnTraceStore, config_fingerprint


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
        trace.mark("context_lookup_start")
        trace.mark("context_lookup_end")
        trace.mark("llm_request_start")
        trace.mark("llm_headers")
        trace.mark("llm_first_content_token")
        trace.mark("llm_speech_segment")
        trace.mark("tts_enqueue")
        trace.mark("tts_first_opus")
        trace.mark("first_ws_binary_sent")
        trace.finish("completed", llm_rounds=2, tool_calls=1)
        summary = recorder.latest_summary()
        self.assertGreaterEqual(summary["turn_start_to_first_ws_binary_ms"], 0)
        self.assertEqual(summary["llm_rounds"], 2)
        self.assertEqual(summary["tool_calls"], 1)
        self.assertIn("context_lookup", summary["latency_ms"])
        self.assertIn("llm_headers", summary["latency_ms"])
        self.assertIn("llm_first_content_token", summary["latency_ms"])
        self.assertIn("llm_first_speech_segment", summary["latency_ms"])
        self.assertIn("tts_first_opus", summary["latency_ms"])
        self.assertIn("tts_opus_to_ws_binary", summary["latency_ms"])

    def test_latency_summary_reports_tool_ready_without_content_token(self):
        recorder = TurnMetricsRecorder("session", {})
        trace = recorder.start_turn(1, "chat")
        trace.mark("llm_request_start")
        trace.mark("llm_headers")
        trace.mark("llm_tool_call_ready")
        trace.mark("tts_enqueue")
        trace.mark("tts_first_opus")
        trace.mark("first_ws_binary_sent")
        trace.finish("completed", llm_rounds=1, tool_calls=1)

        latency = recorder.latest_summary()["latency_ms"]
        self.assertIn("llm_tool_ready", latency)
        self.assertNotIn("llm_first_content_token", latency)
        self.assertIn("tts_first_opus", latency)

    def test_secret_values_do_not_change_config_fingerprint(self):
        left = config_fingerprint({"api_key": "one", "token": "a", "mode": "fast"})
        right = config_fingerprint({"api_key": "two", "token": "b", "mode": "fast"})
        self.assertEqual(left, right)

    def test_shared_trace_store_is_bounded_and_keeps_finished_trace_objects(self):
        store = TurnTraceStore(max_recent=2)
        recorder = TurnMetricsRecorder("session", {}, shared_store=store)

        first = recorder.start_turn(1, "chat")
        first.finish("completed", llm_rounds=1, tool_calls=0)
        second = recorder.start_turn(2, "chat")
        second.finish("cancelled", llm_rounds=1, tool_calls=0)
        third = recorder.start_turn(3, "chat")
        third.finish("failed", llm_rounds=1, tool_calls=0)

        self.assertEqual(len(store.recent), 2)
        self.assertIs(store.recent[0], second)
        self.assertIs(store.recent[1], third)
        self.assertEqual(store.recent[1].outcome, "failed")

    def test_capture_pipeline_events_are_attached_to_turn_trace(self):
        store = TurnTraceStore(max_recent=2)
        recorder = TurnMetricsRecorder("session", {}, shared_store=store)
        recorder.record_capture_event(9, "speech_endpoint", reason="silence")
        recorder.record_capture_event(9, "asr_enqueue", queue_size=1)
        recorder.record_capture_event(9, "asr_lock_acquired", wait_ms=2.5)
        recorder.record_capture_event(9, "asr_infer_end", infer_ms=12.5)
        recorder.record_capture_event(9, "asr_final", text_chars=8)

        trace = recorder.start_turn(9, "chat")
        trace.finish("completed", llm_rounds=1, tool_calls=0)

        names = [event.name for event in trace.events]
        self.assertEqual(
            names[:5],
            ["speech_endpoint", "asr_enqueue", "asr_lock_acquired", "asr_infer_end", "asr_final"],
        )
        self.assertIs(store.recent[-1], trace)
        self.assertEqual(trace.first("asr_infer_end").fields["infer_ms"], 12.5)


if __name__ == "__main__":
    unittest.main()
