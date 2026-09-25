import unittest

from config.settings import AppConfig, _validate_app_config
from core.session import ClientSession
from server import _runtime_bool_value


class FakeWebSocket:
    request = None


class FakeASR:
    async def start(self):
        return None

    async def stop(self):
        return None


class FakeTTS:
    sample_rate = 24000
    frame_duration_ms = 60


class FakeLLM:
    persona_version = 0
    system_prompt = "test"


class SessionForConfigTest(ClientSession):
    def _create_asr(self):
        return FakeASR()


class RuntimeConfigWiringTests(unittest.TestCase):
    def test_session_uses_configured_history_and_read_parallelism(self):
        config = AppConfig()
        config.conversation.history_turns = 9
        config.tools.max_parallel_read_only = 4

        session = SessionForConfigTest(
            FakeWebSocket(),
            config,
            FakeTTS(),
            FakeLLM(),
        )

        self.assertEqual(session.dialogue.max_history_turns, 9)
        self.assertEqual(session.tool_executor._read_slots._value, 4)

    def test_first_audio_target_is_not_used_as_failure_timeout(self):
        config = AppConfig()
        self.assertEqual(config.latency.target_first_audio_ms, 600)
        self.assertGreater(
            config.latency.first_token_timeout_ms,
            config.latency.target_first_audio_ms,
        )

    def test_speculative_asr_requires_start_before_final_endpoint(self):
        config = AppConfig()
        config.asr.speculative_inference_enabled = True
        config.asr.speculative_start_silence_ms = 450
        config.asr.min_silence_duration_ms = 450
        with self.assertRaisesRegex(ValueError, "speculative_start_silence_ms"):
            _validate_app_config(config)

    def test_tts_scheduler_policy_config_bounds(self):
        config = AppConfig()
        config.tts.first_audio_priority_boost = 51.0
        with self.assertRaisesRegex(ValueError, "first_audio_priority_boost"):
            _validate_app_config(config)

        config = AppConfig()
        config.tts.scheduler_aging_per_second = 0.0
        with self.assertRaisesRegex(ValueError, "scheduler_aging_per_second"):
            _validate_app_config(config)

    def test_runtime_boolean_parser_does_not_treat_false_string_as_true(self):
        self.assertFalse(
            _runtime_bool_value("asr.speculative_inference_enabled", "false")
        )
        self.assertTrue(
            _runtime_bool_value("asr.speculative_inference_enabled", "true")
        )
        with self.assertRaises(ValueError):
            _runtime_bool_value("asr.speculative_inference_enabled", "maybe")


if __name__ == "__main__":
    unittest.main()
