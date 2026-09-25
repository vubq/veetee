import json
import pathlib
import unittest

from config.settings import DEFAULT_TIMEZONE
from core.ai_contract import (
    ASR_CORRECTION_PROMPT,
    IDLE_FAREWELL_SYSTEM_PROMPT,
    INLINE_CONVERSATION_CONTROL_PROMPT,
    NO_ACTION_TOOL_NAME,
    RECOVERY_MESSAGE_PROMPT,
    SEMANTIC_SYSTEM_PROMPT,
    semantic_tools,
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
        self.assertIs(GroqDirectLLM.ASR_CORRECTION_PROMPT, ASR_CORRECTION_PROMPT)
        self.assertIs(OmnirouteGroqLLM.ASR_CORRECTION_PROMPT, ASR_CORRECTION_PROMPT)

    def test_control_prompt_fails_open_to_continue(self):
        self.assertIn("[continue]", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("[end]", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("CHÍNH phiên hiện tại", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("[continue] cho mọi trường hợp khác", INLINE_CONVERSATION_CONTROL_PROMPT)
        self.assertIn("hoàn tất tác vụ không đồng nghĩa đóng phiên", INLINE_CONVERSATION_CONTROL_PROMPT)

    def test_clock_snapshot_is_compact_authoritative_data(self):
        message = clock_context(DEFAULT_TIMEZONE)
        content = message["content"]
        self.assertTrue(content.startswith(CLOCK_CONTEXT_PREFIX))
        payload = json.loads(content[len(CLOCK_CONTEXT_PREFIX):])
        self.assertEqual(payload["timezone"], DEFAULT_TIMEZONE)
        self.assertEqual(payload["source"], "server_clock")
        self.assertTrue(payload["date"])
        self.assertTrue(payload["time"])
        self.assertTrue(payload["weekday_vi"].startswith(("Thứ ", "Chủ Nhật")))

    def test_semantic_prompt_forbids_internal_tool_leakage(self):
        self.assertIn("metadata nội bộ", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("tuyệt đối không đọc/hiển thị", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("tự gọi lại tool", SEMANTIC_SYSTEM_PROMPT)

    def test_semantic_prompt_fills_short_followup_slots_without_reasking(self):
        self.assertIn("Follow-up slot filling", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("một cụm ngắn", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("không lặp lại cùng câu hỏi", SEMANTIC_SYSTEM_PROMPT)

    def test_no_action_semantic_tool_is_opt_in_for_structured_gates(self):
        normal = semantic_tools(memory_enabled=False, pending_action=False)
        gated = semantic_tools(
            memory_enabled=False,
            pending_action=False,
            include_no_action=True,
        )
        normal_names = {item["function"]["name"] for item in normal}
        gated_names = {item["function"]["name"] for item in gated}
        self.assertNotIn(NO_ACTION_TOOL_NAME, normal_names)
        self.assertIn(NO_ACTION_TOOL_NAME, gated_names)

    def test_semantic_prompt_owns_clock_behavior(self):
        self.assertIn("server_clock", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("authoritative", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("không gọi tool cho giờ/ngày local", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("timezone khác", SEMANTIC_SYSTEM_PROMPT)
        self.assertIn("không tự thêm giờ/ngày/thứ khác", SEMANTIC_SYSTEM_PROMPT)

    def test_persona_template_does_not_compete_with_lifecycle_format(self):
        template = pathlib.Path("agent-base-prompt.txt").read_text(encoding="utf-8")
        self.assertNotIn("Mỗi câu trả lời bắt đầu bằng đúng một thẻ", template)
        self.assertIn("semantic contract", template)
        self.assertIn("Lifecycle/emotion marker", template)

    def test_idle_farewell_prompt_forbids_reanswering_history(self):
        self.assertIn("lifecycle RIÊNG", IDLE_FAREWELL_SYSTEM_PROMPT)
        self.assertIn("không nhắc lại", IDLE_FAREWELL_SYSTEM_PROMPT)
        self.assertIn("Câu phải mang nghĩa kết thúc rõ ràng", IDLE_FAREWELL_SYSTEM_PROMPT)

    def test_recovery_prompt_requires_complete_sentence(self):
        self.assertIn("câu hoàn chỉnh", RECOVERY_MESSAGE_PROMPT)
        self.assertIn("4-14 từ", RECOVERY_MESSAGE_PROMPT)
        self.assertIn("mảnh câu", RECOVERY_MESSAGE_PROMPT)

    def test_hot_path_static_ai_metadata_stays_compact(self):
        from core.tools.builtin.calculator import calculator_descriptor
        from core.tools.builtin.music_tool import MusicToolProvider
        from core.tools.registry import ToolRegistry

        class _Player:
            pass

        registry = ToolRegistry([
            calculator_descriptor(),
            *MusicToolProvider(_Player()).descriptors(),
        ])
        tools = registry.openai_tools(limit=16) + semantic_tools(
            memory_enabled=True,
            pending_action=False,
        )
        serialized_tools = json.dumps(
            tools,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        total_chars = (
            len(SEMANTIC_SYSTEM_PROMPT)
            + len(INLINE_CONVERSATION_CONTROL_PROMPT)
            + len(serialized_tools)
        )
        self.assertLessEqual(len(SEMANTIC_SYSTEM_PROMPT), 1500)
        self.assertLessEqual(len(INLINE_CONVERSATION_CONTROL_PROMPT), 550)
        self.assertLessEqual(len(serialized_tools), 2200)
        self.assertLessEqual(total_chars, 4250)


if __name__ == "__main__":
    unittest.main()
