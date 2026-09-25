import asyncio
import json
import unittest

from scripts.benchmark_pipeline import RunTrace, _receive_turn


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = iter(messages)

    async def recv(self):
        await asyncio.sleep(0)
        return next(self._messages)


class _UnusedDecoder:
    def decode(self, payload, frame_samples):
        raise AssertionError("decoder should not be used in this test")


class BenchmarkTurnCorrelationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_response_kind_is_captured_without_text_matching(self):
        ws = _FakeWebSocket([
            json.dumps({
                "type": "tts",
                "state": "sentence_start",
                "text": "Một câu recovery bất kỳ.",
                "response_kind": "recovery",
            }),
            json.dumps({"type": "tts", "state": "stop"}),
        ])
        trace = RunTrace(
            run_index=0,
            measured=True,
            mode="auto",
            session_id="session",
        )

        await _receive_turn(
            ws,
            trace,
            1.0,
            response_decoder=_UnusedDecoder(),
            response_sample_rate=24000,
            response_frame_samples=1440,
            voiced_rms_threshold=160.0,
        )

        self.assertEqual(trace.response_kind, "recovery")
        self.assertEqual(trace.first_tts_sentence, "Một câu recovery bất kỳ.")

    async def test_uncorrelated_tts_stop_does_not_end_measured_turn(self):
        ws = _FakeWebSocket([
            json.dumps({"type": "tts", "state": "stop"}),
            json.dumps({
                "type": "stt",
                "text": "Mấy giờ rồi.",
                "speech_final": True,
            }),
            json.dumps({"type": "tts", "state": "stop"}),
        ])
        trace = RunTrace(
            run_index=0,
            measured=True,
            mode="auto",
            session_id="session",
        )

        await _receive_turn(
            ws,
            trace,
            1.0,
            response_decoder=_UnusedDecoder(),
            response_sample_rate=24000,
            response_frame_samples=1440,
            voiced_rms_threshold=160.0,
        )

        self.assertEqual(trace.transcript, "Mấy giờ rồi.")
        self.assertIn("ignored_uncorrelated_tts_stop", trace.marks)
        self.assertIn("tts_stop_received", trace.marks)
        self.assertGreaterEqual(
            trace.marks["tts_stop_received"],
            trace.marks["stt_final_received"],
        )


if __name__ == "__main__":
    unittest.main()
