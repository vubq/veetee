import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from scripts.benchmark_corpus import (
    aggregate_rows,
    corpus_coverage,
    discover_fixtures,
    representative_corpus_errors,
    requested_profile,
)
from scripts.benchmark_endpoint_matrix import MatrixError


class BenchmarkCorpusTests(unittest.TestCase):
    def test_discover_fixtures_requires_transcript_and_speech_end_label(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name, metadata in {
                "good": {
                    "speech_end_sample": 100,
                    "expected_transcript": "xin chào",
                },
                "no_text": {"speech_end_sample": 100},
                "no_end": {"expected_transcript": "xin chào"},
            }.items():
                wav = root / f"{name}.wav"
                with wave.open(str(wav), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(b"\x00\x00" * 200)
                wav.with_suffix(".wav.json").write_text(
                    json.dumps(metadata),
                    encoding="utf-8",
                )

            found = discover_fixtures(root)

        self.assertEqual([item.name for item in found], ["good.wav"])

    def test_requested_profile_validates_speculative_window(self):
        original = {
            "asr.min_silence_duration_ms": 384,
            "asr.speculative_inference_enabled": False,
            "asr.speculative_start_silence_ms": 64,
            "asr.speculative_min_confidence": 0.95,
        }
        args = SimpleNamespace(
            min_silence_ms=192,
            speculative="on",
            speculative_start_silence_ms=64,
            speculative_min_confidence=0.96,
            speculative_llm="off",
            speculative_llm_min_confidence=0.95,
            speculative_tts="on",
            first_soft_cut_chars=8,
            first_soft_cut_min_words=3,
        )
        profile = requested_profile(args, original)
        self.assertEqual(profile["asr.min_silence_duration_ms"], 192)
        self.assertTrue(profile["asr.speculative_inference_enabled"])
        self.assertEqual(profile["asr.speculative_min_confidence"], 0.96)
        self.assertTrue(profile["tts.speculative_prefetch_enabled"])
        self.assertEqual(
            profile["llm.speech_segmentation.first_soft_cut_chars"], 8
        )
        self.assertEqual(
            profile["llm.speech_segmentation.first_soft_cut_min_words"], 3
        )

        args.speculative_start_silence_ms = 192
        with self.assertRaises(MatrixError):
            requested_profile(args, original)

    def test_representative_coverage_reports_missing_categories(self):
        coverage = {
            "fixtures": 4,
            "critical": 1,
            "categories": {"very_short": 4},
            "environments": {"quiet": 4},
        }
        errors = representative_corpus_errors(coverage, min_fixtures=30)
        self.assertTrue(any("fixtures 4 < required 30" in item for item in errors))
        self.assertTrue(any("category internal_pause" in item for item in errors))

    def test_corpus_coverage_reads_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fixtures = []
            for index, category in enumerate(("very_short", "background_noise")):
                wav = root / f"case-{index}.wav"
                with wave.open(str(wav), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(b"\x00\x00" * 200)
                wav.with_suffix(".wav.json").write_text(
                    json.dumps({
                        "speech_end_sample": 100,
                        "expected_transcript": "xin chào",
                        "category": category,
                        "environment": "fan" if index else "quiet",
                        "critical": index == 0,
                    }),
                    encoding="utf-8",
                )
                fixtures.append(wav)

            coverage = corpus_coverage(fixtures)

        self.assertEqual(coverage["fixtures"], 2)
        self.assertEqual(coverage["critical"], 1)
        self.assertEqual(coverage["categories"]["very_short"], 1)
        self.assertEqual(coverage["categories"]["background_noise"], 1)
        self.assertEqual(coverage["environments"]["fan"], 1)

    def test_aggregate_rows_combines_quality_without_picking_winner(self):
        result = aggregate_rows([
            {
                "requested_samples": 2,
                "successful_samples": 2,
                "p50_ms": 800.0,
                "quality": {
                    "labelled_samples": 2,
                    "split_or_missing_final_samples": 0,
                    "wer_mean": 0.0,
                    "cer_mean": 0.0,
                },
            },
            {
                "requested_samples": 2,
                "successful_samples": 1,
                "p50_ms": 950.0,
                "quality": {
                    "labelled_samples": 2,
                    "split_or_missing_final_samples": 1,
                    "wer_mean": 0.25,
                    "cer_mean": 0.1,
                },
            },
        ])
        self.assertEqual(result["requested_samples"], 4)
        self.assertEqual(result["successful_samples"], 3)
        self.assertEqual(result["split_or_missing_final_samples"], 1)
        self.assertEqual(result["mean_fixture_wer"], 0.125)
        self.assertEqual(result["mean_fixture_cer"], 0.05)
        self.assertNotIn("winner", result)


if __name__ == "__main__":
    unittest.main()
