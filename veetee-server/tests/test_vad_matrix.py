import unittest

from scripts.benchmark_endpoint_matrix import MatrixError
from scripts.benchmark_vad_matrix import effective_value, parse_values


class VADMatrixTests(unittest.TestCase):
    def test_float_values(self):
        self.assertEqual(
            parse_values("0.3,0.6,0.8,0.6", setting="asr.vad_end_threshold"),
            [0.3, 0.6, 0.8],
        )

    def test_integer_setting_rejects_fraction(self):
        with self.assertRaises(MatrixError):
            parse_values("1.5", setting="asr.speech_start_frames")

    def test_effective_value_uses_profile_asr(self):
        diagnostics = {"profile": {"asr": {"vad_end_threshold": 0.8}}}
        self.assertEqual(
            effective_value(diagnostics, "asr.vad_end_threshold"),
            0.8,
        )


if __name__ == "__main__":
    unittest.main()
