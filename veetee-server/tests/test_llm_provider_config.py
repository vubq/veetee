import os
import tempfile
import unittest

from config.settings import AppConfig, DEFAULT_TIMEZONE, load_settings


MINIMAL_YAML = """\
server:
  host: "127.0.0.1"
llm:
  provider: "groq"
  model: "qwen/qwen3.6-27b"
  key_pool:
    - {id: "A", api_key_env: "GROQ_API_KEY_A", quota_group: "gA", enabled: true}
    - {id: "B", api_key_env: "GROQ_API_KEY_B", enabled: false}
  quota_groups:
    gA: {rpm: 30, tpm: 8000}
  routing:
    headroom_pct: 10.0
    max_attempts: 2
    admission_wait_ms: 50.0
    discovery_max_inflight: 1
    inflight_penalty_s: 0.4
"""


def write_temp(content):
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write(content)
    handle.close()
    return handle.name


class GroqProviderConfigTests(unittest.TestCase):
    def test_example_config_parses_with_groq_defaults(self):
        config = load_settings("config.example.yaml")
        self.assertEqual(config.llm.provider, "groq")
        self.assertTrue(config.llm.model)
        # Example config demonstrates dynamic env discovery instead of
        # pretending the pool is limited to A-D aliases.
        self.assertEqual(config.llm.key_pool, [])
        self.assertEqual(config.server.timezone, DEFAULT_TIMEZONE)
        self.assertEqual(config.latency.target_first_audio_ms, 600)
        self.assertEqual(config.llm.max_tokens, 320)

    def test_minimal_pool_parses_and_defaults_group(self):
        path = write_temp(MINIMAL_YAML)
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(config.llm.key_pool), 2)
        self.assertEqual(config.llm.key_pool[0].quota_group, "gA")
        # Empty quota_group defaults to g<id>.
        self.assertEqual(config.llm.key_pool[1].quota_group, "gB")
        self.assertFalse(config.llm.key_pool[1].enabled)
        self.assertEqual(config.llm.quota_groups["gA"]["tpm"], 8000)
        self.assertEqual(config.llm.routing.max_attempts, 2)

    def test_rejects_unknown_provider(self):
        path = write_temp(MINIMAL_YAML.replace('provider: "groq"',
                                               'provider: "other"'))
        try:
            with self.assertRaises(ValueError):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_rejects_duplicate_pool_id(self):
        doc = MINIMAL_YAML.replace('id: "B"', 'id: "A"')
        path = write_temp(doc)
        try:
            with self.assertRaises(ValueError):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_rejects_unknown_quota_dimension(self):
        doc = MINIMAL_YAML.replace("tpm: 8000", "per_second: 5")
        path = write_temp(doc)
        try:
            with self.assertRaises(ValueError):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_rejects_bad_routing(self):
        doc = MINIMAL_YAML.replace("max_attempts: 2", "max_attempts: 99")
        path = write_temp(doc)
        try:
            with self.assertRaises(ValueError):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_default_config_uses_groq(self):
        config = AppConfig()
        self.assertEqual(config.llm.provider, "groq")
        self.assertEqual(config.server.timezone, DEFAULT_TIMEZONE)
        self.assertEqual(config.latency.target_first_audio_ms, 600)

    def test_speech_segmentation_nested_config_loads(self):
        path = write_temp(
            "llm:\n"
            "  speech_segmentation:\n"
            "    first_clause_min_chars: 18\n"
            "    first_clause_min_words: 3\n"
            "    clause_target_chars: 140\n"
        )
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)
        self.assertEqual(config.llm.speech_segmentation.first_clause_min_chars, 18)
        self.assertEqual(config.llm.speech_segmentation.first_clause_min_words, 3)
        self.assertEqual(config.llm.speech_segmentation.clause_target_chars, 140)

    def test_speech_segmentation_rejects_invalid_cross_bounds(self):
        path = write_temp(
            "llm:\n"
            "  speech_segmentation:\n"
            "    clause_min_chars: 180\n"
            "    clause_target_chars: 120\n"
        )
        try:
            with self.assertRaisesRegex(ValueError, "clause_min_chars"):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_legacy_context_timeout_migrates_to_memory_owner(self):
        path = write_temp(
            "latency:\n  context_lookup_timeout_ms: 37\n"
            "memory:\n  enabled: true\n"
        )
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)
        self.assertEqual(config.memory.lookup_timeout_ms, 37)

    def test_explicit_memory_timeout_wins_over_legacy_alias(self):
        path = write_temp(
            "latency:\n  context_lookup_timeout_ms: 37\n"
            "memory:\n  lookup_timeout_ms: 11\n"
        )
        try:
            config = load_settings(path)
        finally:
            os.unlink(path)
        self.assertEqual(config.memory.lookup_timeout_ms, 11)

    def test_parakeet_sample_rate_is_an_explicit_invariant(self):
        path = write_temp(
            "asr:\n"
            "  provider: parakeet_silero\n"
            "  sample_rate: 24000\n"
        )
        try:
            with self.assertRaisesRegex(ValueError, "requires asr.sample_rate=16000"):
                load_settings(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
