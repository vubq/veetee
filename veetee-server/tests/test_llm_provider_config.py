import os
import tempfile
import unittest

from config.settings import AppConfig, load_settings


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
        self.assertGreaterEqual(len(config.llm.key_pool), 1)
        for entry in config.llm.key_pool:
            self.assertTrue(entry.id)
            self.assertTrue(entry.api_key_env)
            self.assertTrue(entry.quota_group)

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
        doc = MINIMAL_YAML.replace("max_attempts: 2", "max_attempts: 9")
        path = write_temp(doc)
        try:
            with self.assertRaises(ValueError):
                load_settings(path)
        finally:
            os.unlink(path)

    def test_default_config_uses_groq(self):
        self.assertEqual(AppConfig().llm.provider, "groq")


if __name__ == "__main__":
    unittest.main()
