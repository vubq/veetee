import json
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.benchmark_pipeline import (
    RunTrace,
    load_fixture,
    summarize,
    transcript_error_rates,
)


class BenchmarkQualityTests(unittest.TestCase):
    def test_transcript_error_rates_normalize_case_and_punctuation(self):
        wer, cer = transcript_error_rates("Mấy giờ rồi.", "mấy giờ rồi?")
        self.assertEqual(wer, 0.0)
        self.assertEqual(cer, 0.0)

    def test_transcript_error_rates_report_word_and_character_errors(self):
        wer, cer = transcript_error_rates("Hôm nay là thường mấy", "Hôm nay thứ mấy")
        self.assertGreater(wer, 0.0)
        self.assertGreater(cer, 0.0)

    def test_load_fixture_reads_quality_labels_from_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fixture.wav"
            with wave.open(str(path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 1600)
            path.with_suffix(".wav.json").write_text(
                json.dumps({
                    "speech_end_sample": 1200,
                    "expected_transcript": "Xin chào",
                }),
                encoding="utf-8",
            )

            fixture = load_fixture(path, None)

        self.assertEqual(fixture.speech_end_sample, 1200)
        self.assertEqual(fixture.expected_transcript, "Xin chào")

    def test_summary_reports_split_and_quality_metrics(self):
        complete = RunTrace(
            run_index=0,
            measured=True,
            mode="auto",
            transcript="Mấy giờ rồi.",
            final_transcripts=["Mấy giờ rồi."],
            expected_transcript="Mấy giờ rồi.",
            word_error_rate=0.0,
            char_error_rate=0.0,
            outcome="completed",
        )
        complete.marks.update({
            "speech_end": 1.0,
            "first_voiced_pcm_received": 1.5,
        })
        split = RunTrace(
            run_index=1,
            measured=True,
            mode="auto",
            transcript="Xin chào.",
            final_transcripts=["Xin chào.", "Bạn tên là gì?"],
            expected_transcript="Xin chào bạn tên là gì?",
            outcome="failed",
            error="split",
        )
        records = [complete.record(), split.record()]
        summary = summarize(records)

        self.assertEqual(summary["quality"]["labelled_samples"], 2)
        self.assertEqual(summary["quality"]["split_or_missing_final_samples"], 1)
        self.assertEqual(summary["quality"]["wer_mean"], 0.0)
        self.assertEqual(summary["quality"]["cer_mean"], 0.0)


if __name__ == "__main__":
    unittest.main()
