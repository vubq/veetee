import json
import unittest
from pathlib import Path

from scripts.benchmark_endpoint_matrix import MatrixError
from scripts.benchmark_tts_buffer_matrix import parse_values, row_from_result
from scripts.build_semantic_review_queue import (
    MIN_CRITICAL,
    MIN_HELD_OUT,
    TARGET_CASES,
    build_queue,
    generated_candidates,
    validate_queue,
)
from scripts.semantic_review_probe import load_cases


class SemanticReviewQueueTests(unittest.TestCase):
    def test_generated_queue_meets_review_coverage_without_claiming_gold(self):
        seed = json.loads(
            Path("eval/semantic_corpus_seed.json").read_text(encoding="utf-8")
        )
        queue = build_queue(seed)
        summary = validate_queue(queue)

        self.assertEqual(summary["cases"], TARGET_CASES)
        self.assertGreaterEqual(summary["critical"], MIN_CRITICAL)
        self.assertGreaterEqual(summary["held_out"], MIN_HELD_OUT)
        self.assertTrue(
            all(item["review_status"] == "needs_human_review" for item in queue)
        )
        self.assertEqual(len({item["id"] for item in queue}), TARGET_CASES)
        self.assertEqual(len({item["user"].casefold() for item in queue}), TARGET_CASES)

    def test_generated_candidates_are_eval_data_not_runtime_rules(self):
        rows = generated_candidates()
        self.assertGreater(len(rows), 100)
        self.assertTrue(all("expected_semantic_outcome" in item for item in rows))
        self.assertTrue(all("user" in item for item in rows))

    def test_review_probe_excludes_held_out_by_default(self):
        selected = load_cases(
            "eval/semantic_corpus_review_queue.json",
            include_held_out=False,
            ids=set(),
            group="",
            limit=200,
        )
        self.assertTrue(selected)
        self.assertTrue(all(item.get("split") != "held-out" for item in selected))

        with self.assertRaises(MatrixError):
            load_cases(
                "eval/semantic_corpus_review_queue.json",
                include_held_out=False,
                ids={"neg-003"},
                group="",
                limit=10,
            )

        held_out = load_cases(
            "eval/semantic_corpus_review_queue.json",
            include_held_out=True,
            ids={"neg-003"},
            group="",
            limit=10,
        )
        self.assertEqual([item["id"] for item in held_out], ["neg-003"])


class TTSBufferMatrixTests(unittest.TestCase):
    def test_parse_values_validates_and_deduplicates(self):
        self.assertEqual(parse_values("4,16,64,16"), [4, 16, 64])
        with self.assertRaises(Exception):
            parse_values("0")
        with self.assertRaises(Exception):
            parse_values("257")

    def test_row_keeps_authoritative_server_metrics(self):
        result = {
            "success_rate": 1.0,
            "protocol_success_rate": 1.0,
            "trace_complete": True,
            "server_metrics": {
                "turn_start_to_first_ws_binary_ms": {"p50_ms": 500},
                "tts_queue_to_lock": {"p50_ms": 20},
                "tts_first_pcm": {"p50_ms": 100},
                "tts_lease_held": {"p50_ms": 900},
                "outcomes": {"completed": 2},
            },
        }
        row = row_from_result(4, result)
        self.assertEqual(row["value_chunks"], 4)
        self.assertEqual(row["tts_queue_to_lock_ms"]["p50_ms"], 20)
        self.assertEqual(row["tts_lease_held_ms"]["p50_ms"], 900)
        self.assertEqual(row["outcomes"], {"completed": 2})


if __name__ == "__main__":
    unittest.main()
