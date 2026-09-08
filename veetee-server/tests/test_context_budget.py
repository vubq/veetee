import unittest

from core.context_builder import ContextBudgetError, ContextBuilder


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.builder = ContextBuilder()

    def test_trims_old_turns_but_preserves_current_user_group(self):
        messages = [
            {"role": "user", "content": "cũ " * 250},
            {"role": "assistant", "content": "trả lời cũ " * 120},
            {"role": "user", "content": "câu hỏi hiện tại"},
        ]

        fitted = self.builder.fit_to_budget(
            messages,
            system_messages=["Bạn là VeeTee."],
            tools=[],
            max_context_tokens=180,
            reserve_output_tokens=20,
            chars_per_token=4,
        )

        self.assertEqual(fitted, [{"role": "user", "content": "câu hỏi hiện tại"}])
        self.assertGreater(self.builder.last_budget["trimmed_groups"], 0)

    def test_tool_call_and_result_stay_with_current_user_group(self):
        messages = [
            {"role": "user", "content": "lịch sử " * 160},
            {"role": "assistant", "content": "cũ " * 100},
            {"role": "user", "content": "mấy giờ rồi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "time", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "time", "content": "21:00"},
        ]

        fitted = self.builder.fit_to_budget(
            messages,
            system_messages=["Bạn là VeeTee."],
            tools=[{"type": "function", "function": {"name": "time"}}],
            max_context_tokens=260,
            reserve_output_tokens=20,
            chars_per_token=4,
        )

        self.assertEqual([item["role"] for item in fitted], ["user", "assistant", "tool"])
        self.assertEqual(fitted[0]["content"], "mấy giờ rồi")

    def test_current_input_too_large_fails_finitely(self):
        with self.assertRaises(ContextBudgetError):
            self.builder.fit_to_budget(
                [{"role": "user", "content": "x" * 2000}],
                system_messages=["Bạn là VeeTee."],
                tools=[],
                max_context_tokens=120,
                reserve_output_tokens=20,
                chars_per_token=4,
            )

    def test_fixed_system_tools_and_output_reserve_are_counted(self):
        with self.assertRaises(ContextBudgetError):
            self.builder.fit_to_budget(
                [{"role": "user", "content": "xin chào"}],
                system_messages=["s" * 200],
                tools=[{"type": "function", "function": {"name": "x", "description": "d" * 400}}],
                max_context_tokens=180,
                reserve_output_tokens=80,
                chars_per_token=4,
            )


if __name__ == "__main__":
    unittest.main()
