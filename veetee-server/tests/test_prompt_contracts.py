import unittest

from core.ai_contract import (
    ASR_CORRECTION_PROMPT,
    IDLE_FAREWELL_SYSTEM_PROMPT,
    INLINE_CONVERSATION_CONTROL_PROMPT,
    RECOVERY_MESSAGE_PROMPT,
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
        self.assertIn("kết thúc CHÍNH phiên hội thoại", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("Hoàn thành một tác vụ KHÔNG đồng nghĩa kết thúc phiên", INLINE_CONVERSATION_CONTROL_PROMPT)

    def test_clock_snapshot_has_readable_authoritative_line(self):
        message = clock_context("Asia/Bangkok")
        content = message["content"]
        self.assertTrue(content.startswith(CLOCK_CONTEXT_PREFIX))
        self.assertIn("ĐỒNG HỒ CHUẨN", content)
        self.assertIn("cấm bịa", content)
        self.assertIn("hỏi thứ", content)
        self.assertIn("không tự thêm ngày hoặc giờ", content)

    def test_semantic_prompt_requires_reading_server_clock(self):
        self.assertIn("server_clock", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("cấm bịa", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("hỏi thứ thì chỉ nói thứ", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("Không tự kèm ngày/thứ/giờ khác", SEMANTIC_SYSTEM_PROMPT)

    def test_idle_farewell_prompt_forbids_reanswering_history(self):
        self.assertIn("lifecycle RIÊNG", IDLE_FAREWELL_SYSTEM_PROMPT)
        self.assertIn("không nhắc lại", IDLE_FAREWELL_SYSTEM_PROMPT)
        self.assertIn("Câu phải mang nghĩa kết thúc rõ ràng", IDLE_FAREWELL_SYSTEM_PROMPT)

    def test_recovery_prompt_requires_complete_sentence(self):
        self.assertIn("câu hoàn chỉnh", RECOVERY_MESSAGE_PROMPT)
        self.assertIn("4-14 từ", RECOVERY_MESSAGE_PROMPT)
        self.assertIn("mảnh câu", RECOVERY_MESSAGE_PROMPT)


if __name__ == "__main__":
    unittest.main()
