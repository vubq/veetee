import unittest

from scripts.benchmark_endpoint_matrix import MatrixError
from scripts.benchmark_speech_segmentation import (
    effective_value,
    parse_values,
)


class SpeechSegmentationMatrixTests(unittest.TestCase):
    def test_parse_values_deduplicates_and_preserves_order(self):
        self.assertEqual(
            parse_values(
                "36,24,18,24",
                setting="llm.speech_segmentation.first_clause_min_chars",
            ),
            [36, 24, 18],
        )

    def test_parse_values_rejects_out_of_range(self):
        with self.assertRaises(MatrixError):
            parse_values(
                "0",
                setting="llm.speech_segmentation.first_clause_min_words",
            )

    def test_soft_cut_matrix_accepts_disabled_zero(self):
        self.assertEqual(
            parse_values(
                "0,8,12,8",
                setting="llm.speech_segmentation.first_soft_cut_chars",
            ),
            [0, 8, 12],
        )

    def test_effective_value_reads_diagnostics_profile(self):
        diagnostics = {
            "profile": {
                "speech_segmentation": {
                    "first_clause_min_chars": 24,
                }
            }
        }
        self.assertEqual(
            effective_value(
                diagnostics,
                "llm.speech_segmentation.first_clause_min_chars",
            ),
            24,
        )

    def test_effective_value_fails_closed_when_missing(self):
        with self.assertRaises(MatrixError):
            effective_value(
                {"profile": {}},
                "llm.speech_segmentation.first_clause_min_chars",
            )


if __name__ == "__main__":
    unittest.main()
