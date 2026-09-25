import unittest

from core.turn_metrics import (
    TurnMetricsRecorder,
    TurnTraceStore,
    config_fingerprint,
    normalize_llm_usage,
)


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
        trace.mark("tts_lock_acquired", queue_wait_ms=0.2)
        trace.mark("tts_first_pcm")
        trace.mark("tts_first_opus")
        trace.mark("tts_inference_done", inference_ms=1.0)
        trace.mark("tts_lease_held", held_ms=7.5)
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
        self.assertIn("tts_queue_to_lock", summary["latency_ms"])
        self.assertIn("tts_first_pcm", summary["latency_ms"])
        self.assertIn("tts_first_pcm_to_opus", summary["latency_ms"])
        self.assertIn("tts_inference", summary["latency_ms"])
        self.assertEqual(summary["latency_ms"]["tts_lease_held"], 7.5)
        self.assertIn("tts_first_opus", summary["latency_ms"])
        self.assertIn("tts_opus_to_ws_binary", summary["latency_ms"])

    def test_repeated_tts_events_include_aggregate_stats(self):
        recorder = TurnMetricsRecorder("session", {})
        trace = recorder.start_turn(1, "chat")
        for queue_wait, held, infer in (
            (10.0, 100.0, 80.0),
            (30.0, 300.0, 250.0),
            (20.0, 200.0, 160.0),
        ):
            trace.mark("tts_lock_acquired", queue_wait_ms=queue_wait)
            trace.mark("tts_inference_done", inference_ms=infer)
            trace.mark("tts_lease_held", held_ms=held)
        trace.finish("completed", llm_rounds=1, tool_calls=0)

        aggregates = recorder.latest_summary()["stage_aggregates_ms"]
        self.assertEqual(
            aggregates["tts_queue_wait"],
            {"count": 3, "total_ms": 60.0, "max_ms": 30.0, "p50_ms": 20.0},
        )
        self.assertEqual(
            aggregates["tts_lease_held"],
            {"count": 3, "total_ms": 600.0, "max_ms": 300.0, "p50_ms": 200.0},
        )
        self.assertEqual(
            aggregates["tts_inference"],
            {"count": 3, "total_ms": 490.0, "max_ms": 250.0, "p50_ms": 160.0},
        )

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

    def test_normalize_llm_usage_reads_nested_cache_details(self):
        fields = normalize_llm_usage({
            "prompt_tokens": 100,
            "completion_tokens": 12,
            "total_tokens": 112,
            "prompt_tokens_details": {"cached_tokens": 75},
        })
        self.assertEqual(fields["prompt_tokens"], 100)
        self.assertEqual(fields["completion_tokens"], 12)
        self.assertEqual(fields["cached_prompt_tokens"], 75)
        self.assertEqual(fields["prompt_cache_ratio"], 0.75)

    def test_normalize_llm_usage_supports_openai_input_aliases(self):
        fields = normalize_llm_usage({
            "input_tokens": 80,
            "output_tokens": 10,
            "input_tokens_details": {"cached_tokens": 20},
        })
        self.assertEqual(fields["prompt_tokens"], 80)
        self.assertEqual(fields["completion_tokens"], 10)
        self.assertEqual(fields["cached_prompt_tokens"], 20)
        self.assertEqual(fields["prompt_cache_ratio"], 0.25)

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
