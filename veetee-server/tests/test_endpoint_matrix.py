import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.benchmark_endpoint_matrix import (
    MatrixError,
    benchmark_run_lock,
    cleanup_benchmark_device,
    effective_endpoint_value,
    ensure_benchmark_device_token,
    matrix_row,
    parse_values,
    runtime_restore_mismatches,
    server_latency_summary,
    wait_for_quiescent_sessions,
)


class EndpointMatrixTests(unittest.TestCase):
    def test_parse_values_deduplicates_and_preserves_order(self):
        self.assertEqual(
            parse_values("450, 320,256,320,192", setting="asr.min_silence_duration_ms"),
            [450, 320, 256, 192],
        )

    def test_parse_values_rejects_out_of_range(self):
        with self.assertRaises(MatrixError):
            parse_values("64", setting="asr.min_silence_duration_ms")
        with self.assertRaises(MatrixError):
            parse_values("50", setting="asr.endpointing_ms")

    def test_effective_value_uses_diagnostics_not_persisted_runtime(self):
        diagnostics = {
            "profile": {
                "asr": {
                    "min_silence_duration_ms": 320,
                    "endpointing_ms": 225,
                }
            }
        }
        self.assertEqual(
            effective_endpoint_value(
                diagnostics, "asr.min_silence_duration_ms"
            ),
            320,
        )
        self.assertEqual(
            effective_endpoint_value(diagnostics, "asr.endpointing_ms"),
            225,
        )

    def test_runtime_restore_mismatches_uses_effective_diagnostics(self):
        diagnostics = {
            "profile": {
                "asr": {
                    "min_silence_duration_ms": 192,
                    "vad_end_threshold": 0.2,
                    "speculative_inference_enabled": True,
                },
                "tts": {"speculative_prefetch_enabled": False},
                "speech_segmentation": {
                    "first_soft_cut_chars": 8,
                    "first_soft_cut_min_words": 3,
                },
            }
        }
        expected = {
            "asr.min_silence_duration_ms": 192,
            "asr.vad_end_threshold": 0.2,
            "asr.speculative_inference_enabled": True,
            "tts.speculative_prefetch_enabled": False,
            "llm.speech_segmentation.first_soft_cut_chars": 8,
            "llm.speech_segmentation.first_soft_cut_min_words": 3,
        }
        self.assertEqual(runtime_restore_mismatches(diagnostics, expected), {})

        diagnostics["profile"]["asr"]["min_silence_duration_ms"] = 160
        diagnostics["profile"]["tts"]["speculative_prefetch_enabled"] = True
        self.assertEqual(
            runtime_restore_mismatches(diagnostics, expected),
            {
                "asr.min_silence_duration_ms": {
                    "expected": 192,
                    "actual": 160,
                },
                "tts.speculative_prefetch_enabled": {
                    "expected": False,
                    "actual": True,
                },
            },
        )

    def test_matrix_row_never_declares_quality_winner(self):
        row = matrix_row(
            256,
            {
                "requested_samples": 100,
                "successful_samples": 100,
                "success_rate": 1.0,
                "p50_ms": 520.0,
                "p90_ms": 800.0,
                "p95_ms": 900.0,
                "max_ms": 1100.0,
                "sla_status": "ACHIEVED",
                "stage_latency_ms": {"speech_end_to_stt_final_ms": {"p50": 260}},
            },
        )
        self.assertEqual(row["value_ms"], 256)
        self.assertEqual(row["p50_ms"], 520.0)
        self.assertNotIn("winner", row)
        self.assertNotIn("recommended", row)

    def test_benchmark_run_lock_rejects_concurrent_hot_config_matrix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = os.path.join(temp_dir, "matrix.lock")
            previous = os.environ.get("VEETEE_BENCHMARK_LOCK_FILE")
            os.environ["VEETEE_BENCHMARK_LOCK_FILE"] = lock_path
            try:
                with benchmark_run_lock("first"):
                    with open(lock_path, encoding="utf-8") as lock_file:
                        self.assertIn("label=first", lock_file.read())
                    with self.assertRaises(MatrixError):
                        with benchmark_run_lock("second"):
                            pass
                with open(lock_path, encoding="utf-8") as lock_file:
                    self.assertEqual(lock_file.read(), "")
            finally:
                if previous is None:
                    os.environ.pop("VEETEE_BENCHMARK_LOCK_FILE", None)
                else:
                    os.environ["VEETEE_BENCHMARK_LOCK_FILE"] = previous

    def test_server_latency_summary_matches_measured_session_turns(self):
        result = server_latency_summary(
            {
                "runtime": {
                    "latest_turns": [
                        {
                            "session_id": "other",
                            "source": "asr",
                            "latency_ms": {"asr_infer": 999},
                        },
                        {
                            "session_id": "bench",
                            "source": "asr",
                            "latency_ms": {
                                "asr_infer": 52.0,
                                "last_voice_to_first_ws_binary": 820.0,
                            },
                        },
                        {
                            "session_id": "bench",
                            "source": "asr",
                            "latency_ms": {
                                "asr_infer": 60.0,
                                "last_voice_to_first_ws_binary": 780.0,
                            },
                        },
                    ]
                }
            },
            [
                {"session_id": "bench", "measured": True},
                {"session_id": "bench", "measured": True},
            ],
        )
        self.assertEqual(result["asr_infer"]["samples"], 2)
        self.assertEqual(result["asr_infer"]["p50"], 52.0)
        self.assertEqual(result["asr_infer"]["p95"], 60.0)


class EndpointMatrixAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        for key in (
            "VEETEE_DEVICE_TOKEN",
            "VEETEE_DEVICE_ID",
            "VEETEE_CLIENT_ID",
            "_VEETEE_BENCHMARK_TEMP_DEVICE",
        ):
            os.environ.pop(key, None)

    async def test_wait_for_quiescent_sessions_polls_until_zero(self):
        responses = [
            {"server": {"active_sessions": 2}},
            {"server": {"active_sessions": 1}},
            {"server": {"active_sessions": 0}},
        ]
        with patch(
            "scripts.benchmark_endpoint_matrix.request_json",
            new=AsyncMock(side_effect=responses),
        ) as request_mock, patch(
            "scripts.benchmark_endpoint_matrix.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            await wait_for_quiescent_sessions(
                "http://127.0.0.1:8003",
                "manager-token",
                timeout_seconds=2,
            )

        self.assertEqual(request_mock.await_count, 3)
        self.assertEqual(sleep_mock.await_count, 2)

    async def test_wait_for_quiescent_sessions_respects_existing_baseline(self):
        responses = [
            {"server": {"active_sessions": 3}},
            {"server": {"active_sessions": 2}},
            {"server": {"active_sessions": 1}},
        ]
        with patch(
            "scripts.benchmark_endpoint_matrix.request_json",
            new=AsyncMock(side_effect=responses),
        ) as request_mock, patch(
            "scripts.benchmark_endpoint_matrix.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep_mock:
            await wait_for_quiescent_sessions(
                "http://127.0.0.1:8003",
                "manager-token",
                timeout_seconds=2,
                max_active_sessions=1,
            )

        self.assertEqual(request_mock.await_count, 3)
        self.assertEqual(sleep_mock.await_count, 2)

    async def test_ephemeral_pairing_uses_unique_identity_and_cleanup_revokes(self):
        calls = []

        async def fake_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/ota/") and len(
                [item for item in calls if item[1].endswith("/ota/")]
            ) == 1:
                return {"activation": {"code": "123456"}}
            if url.endswith("/ota/activate"):
                if len(
                    [item for item in calls if item[1].endswith("/ota/activate")]
                ) == 1:
                    raise MatrixError("POST /ota/activate failed HTTP 202: pending")
                return {"status": "activated"}
            if url.endswith("/api/devices/pair"):
                return {"device": {"device_id": "benchmark"}}
            if url.endswith("/ota/"):
                return {"websocket": {"token": "device-token"}}
            if url.endswith("/revoke"):
                return {"device": {"revoked": True}}
            raise AssertionError(url)

        with patch(
            "scripts.benchmark_endpoint_matrix.request_json",
            new=AsyncMock(side_effect=fake_request),
        ), patch(
            "scripts.benchmark_endpoint_matrix.uuid.uuid4",
            return_value=SimpleNamespace(hex="abc123def4567890"),
        ):
            token = await ensure_benchmark_device_token(
                "http://127.0.0.1:8003",
                "manager-token",
            )
            self.assertEqual(token, "device-token")
            self.assertEqual(
                os.environ["VEETEE_DEVICE_ID"],
                "veetee-benchmark-abc123def456",
            )
            self.assertEqual(
                os.environ["VEETEE_CLIENT_ID"],
                "benchmark-client-abc123def456",
            )
            self.assertEqual(
                os.environ["_VEETEE_BENCHMARK_TEMP_DEVICE"],
                "1",
            )

            error = await cleanup_benchmark_device(
                "http://127.0.0.1:8003",
                "manager-token",
            )

        self.assertEqual(error, "")
        self.assertNotIn("VEETEE_DEVICE_TOKEN", os.environ)
        self.assertTrue(any(url.endswith("/revoke") for _, url, _ in calls))


if __name__ == "__main__":
    unittest.main()
