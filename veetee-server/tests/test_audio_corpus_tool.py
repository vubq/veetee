import json
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.audio_corpus_tool import (
    MIN_CASES,
    PLAN_CASES,
    REQUIRED_CATEGORIES,
    validate_corpus,
    write_plan,
)


class AudioCorpusToolTests(unittest.TestCase):
    def test_plan_meets_minimum_coverage(self):
        self.assertGreaterEqual(len(PLAN_CASES), MIN_CASES)
        counts = {}
        for item in PLAN_CASES:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        for category, required in REQUIRED_CATEGORIES.items():
            self.assertGreaterEqual(counts.get(category, 0), required)

    def test_write_plan_is_reviewable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            payload = write_plan(path)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["cases"]), len(loaded["cases"]))
            self.assertTrue(any("người thật" in x for x in loaded["instructions"]))

    def test_validate_accepts_complete_fixture_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = []
            for index, item in enumerate(PLAN_CASES[:MIN_CASES]):
                wav = root / f"case-{index:02d}.wav"
                with wave.open(str(wav), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(b"\x00\x00" * 1600)
                sidecar = {
                    "expected_transcript": item["text"],
                    "speech_end_sample": 1500,
                    "category": item["category"],
                    "critical": item.get("critical", False),
                }
                wav.with_suffix(".wav.json").write_text(
                    json.dumps(sidecar, ensure_ascii=False),
                    encoding="utf-8",
                )
                cases.append(item)

            report = validate_corpus(root)
            # The first 30 plan entries deliberately contain >=3 of every
            # required coverage class.
            self.assertTrue(report["valid"], report["errors"])
            self.assertEqual(report["fixtures"], MIN_CASES)

    def test_validate_rejects_missing_sidecar_and_bad_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "broken.wav"
            with wave.open(str(wav), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 3200)
            report = validate_corpus(root)
            self.assertFalse(report["valid"])
            self.assertTrue(any("missing sidecar" in x for x in report["errors"]))


if __name__ == "__main__":
    unittest.main()
