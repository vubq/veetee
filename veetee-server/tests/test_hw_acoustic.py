import re
import unittest

from scripts.hw_acoustic_test import (
    DISCONNECT_SERIAL_RE,
    SPEAKING_SERIAL_RE,
    TTS_SENT_RE,
    WAKE_SERIAL_RE,
    finish,
    parse_transcript,
)


class HwAcousticHelperTests(unittest.TestCase):
    def test_parse_transcript_extracts_text(self):
        line = ("10:51:06 [INFO] [ParakeetSileroASR] Parakeet final "
                "transcript: 'Mấy giờ rồi.' (min_word_confidence=0.826)")
        self.assertEqual(parse_transcript(line), "Mấy giờ rồi.")

    def test_parse_transcript_ignores_other_lines(self):
        self.assertIsNone(parse_transcript("Session 6d08 closed"))
        self.assertIsNone(parse_transcript(""))

    def test_serial_markers_match_device_logs(self):
        self.assertTrue(WAKE_SERIAL_RE.search(
            "Application: Wake word detected: Hi,ESP"))
        self.assertTrue(SPEAKING_SERIAL_RE.search(
            "StateMachine: State: listening -> speaking"))
        self.assertTrue(DISCONNECT_SERIAL_RE.search(
            "WS: Websocket disconnected"))
        self.assertFalse(WAKE_SERIAL_RE.search(
            "SystemInfo: free sram: 132163"))

    def test_tts_sent_marker(self):
        self.assertTrue(TTS_SENT_RE.search(
            "Post-ASR first TTS binary sent in 0.742s"))

    def test_finish_exit_codes(self):
        self.assertEqual(finish([("wake", True, "ok")]), 0)
        self.assertEqual(finish([("wake", True, "ok"),
                                 ("stt", False, "empty")]), 1)


if __name__ == "__main__":
    unittest.main()
