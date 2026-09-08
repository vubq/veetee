import os
import tempfile
import textwrap
import unittest

from config.settings import AppConfig, ConversationConfig, load_settings
from core.conversation import classify_conversation_text, normalize_command_text


class ConversationPolicyTests(unittest.TestCase):
    def test_normalization_keeps_vietnamese_and_strips_boundary_punctuation(self):
        self.assertEqual(normalize_command_text("  ...VeeTee ƠI!!!  "), "veetee ơi")
        self.assertEqual(normalize_command_text("你好小智。"), "你好小智")

    def test_exact_wake_only(self):
        config = ConversationConfig(enabled=True, wake_words=["VeeTee ơi"])
        self.assertEqual(
            classify_conversation_text("VeeTee ơi!", config, source="detect").kind,
            "wake",
        )
        self.assertEqual(
            classify_conversation_text("VeeTee ơi, thời tiết thế nào?", config, source="detect").kind,
            "chat",
        )

    def test_exit_is_exact_not_substring(self):
        config = ConversationConfig(enabled=True, exit_commands=["tạm biệt", "kết thúc trò chuyện"])
        self.assertEqual(
            classify_conversation_text("Tạm biệt!", config, source="asr", allow_wake=False).kind,
            "exit",
        )
        self.assertEqual(
            classify_conversation_text("Đừng kết thúc trò chuyện", config, source="asr", allow_wake=False).kind,
            "chat",
        )
        self.assertEqual(
            classify_conversation_text("Giải thích từ tạm biệt", config, source="text", allow_wake=False).kind,
            "chat",
        )

    def test_feature_off_preserves_chat_route(self):
        config = ConversationConfig(enabled=False)
        self.assertEqual(
            classify_conversation_text("VeeTee ơi", config, source="detect").kind,
            "chat",
        )

    def test_invalid_types_and_overlap_fail_fast(self):
        cases = [
            "conversation:\n  enabled: yes-please\n",
            "conversation:\n  idle_timeout_seconds: -1\n",
            "conversation:\n  wake_words: [\"VeeTee ơi\", \"...veetee ƠI!!!\"]\n",
            "conversation:\n  wake_words: [\"tạm biệt\"]\n  exit_commands: [\"TẠM BIỆT!\"]\n",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as f:
                    f.write(raw)
                    path = f.name
                try:
                    with self.assertRaises(ValueError):
                        load_settings(path)
                finally:
                    os.unlink(path)

    def test_missing_conversation_group_uses_disabled_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as f:
            f.write("server:\n  ws_port: 8123\n")
            path = f.name
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)
        self.assertEqual(config.server.ws_port, 8123)
        self.assertFalse(config.conversation.enabled)


if __name__ == "__main__":
    unittest.main()
