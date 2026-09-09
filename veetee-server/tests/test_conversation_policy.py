import os
import tempfile
import unittest

from config.settings import ConversationConfig, load_settings


class ConversationPolicyTests(unittest.TestCase):
    def test_legacy_wake_and_exit_fields_are_inert_compatibility_data(self):
        config = ConversationConfig(
            enabled=True,
            wake_words=["VeeTee ơi", "...veetee ƠI!!!"],
            exit_commands=["tạm biệt", "TẠM BIỆT!"],
        )

        self.assertEqual(config.wake_words[0], "VeeTee ơi")
        self.assertEqual(config.exit_commands[0], "tạm biệt")

    def test_config_accepts_legacy_overlap_without_semantic_matcher_validation(self):
        raw = (
            "conversation:\n"
            "  enabled: true\n"
            "  wake_words: [\"tạm biệt\"]\n"
            "  exit_commands: [\"TẠM BIỆT!\"]\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8", delete=False) as f:
            f.write(raw)
            path = f.name
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)

        self.assertEqual(config.conversation.wake_words, ["tạm biệt"])
        self.assertEqual(config.conversation.exit_commands, ["TẠM BIỆT!"])

    def test_invalid_types_still_fail_fast(self):
        cases = [
            "conversation:\n  enabled: yes-please\n",
            "conversation:\n  idle_timeout_seconds: -1\n",
            "conversation:\n  wake_words: 123\n",
            "conversation:\n  exit_commands: [\"\"]\n",
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
