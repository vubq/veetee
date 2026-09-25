import unittest

from scripts.benchmark_model_matrix import MatrixError, effective_model, parse_models


class ModelMatrixTests(unittest.TestCase):
    def test_parse_models_deduplicates_preserving_order(self):
        self.assertEqual(
            parse_models("qwen/qwen3.8-27b, openai/gpt-oss-20b, qwen/qwen3.8-27b"),
            ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        )

    def test_parse_models_rejects_empty(self):
        with self.assertRaises(MatrixError):
            parse_models(" , ")

    def test_effective_model_prefers_runtime_health(self):
        self.assertEqual(
            effective_model({
                "llm": {"model": "openai/gpt-oss-20b"},
                "profile": {"llm": {"model": "qwen/qwen3.8-27b"}},
            }),
            "openai/gpt-oss-20b",
        )


if __name__ == "__main__":
    unittest.main()
