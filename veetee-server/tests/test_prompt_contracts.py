import unittest

from core.ai_contract import (
    ASR_CORRECTION_PROMPT,
    INLINE_CONVERSATION_CONTROL_PROMPT,
    SEMANTIC_SYSTEM_PROMPT,
)
from core.clock_context import CLOCK_CONTEXT_PREFIX, clock_context


class PromptContractTests(unittest.TestCase):
    def test_providers_share_single_control_prompt_source(self):
        from core.providers.llm.groq_direct import GroqDirectLLM
        from core.providers.llm.omniroute_groq import OmnirouteGroqLLM

        self.assertIs(
            GroqDirectLLM.INLINE_CONVERSATION_CONTROL_PROMPT,
            INLINE_CONVERSATION_CONTROL_PROMPT,
        )
        self.assertIs(
            OmnirouteGroqLLM.INLINE_CONVERSATION_CONTROL_PROMPT,
            INLINE_CONVERSATION_CONTROL_PROMPT,
        )
        self.assertIs(
            GroqDirectLLM.ASR_CORRECTION_PROMPT, ASR_CORRECTION_PROMPT)
        self.assertIs(
            OmnirouteGroqLLM.ASR_CORRECTION_PROMPT, ASR_CORRECTION_PROMPT)

    def test_control_prompt_fails_open_to_continue(self):
        self.assertIn("[continue]", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("[end]", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("không bao giờ chọn [end]", INLINE_CONVERSATION_CONTROL_PROMPT)

    def test_clock_snapshot_has_readable_authoritative_line(self):
        message = clock_context("Asia/Bangkok")
        content = message["content"]
        self.assertTrue(content.startswith(CLOCK_CONTEXT_PREFIX))
        self.assertIn("ĐỒNG HỒ CHUẨN", content)
        self.assertIn("cấm bịa", content)

    def test_semantic_prompt_requires_reading_server_clock(self):
        self.assertIn("server_clock", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("cấm bịa", SEMANTIC_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
