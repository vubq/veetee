"""M0.2/M7 regression: A01/A02/A03/A04/A05/A09 fail-before-fix cases.

Each test pins the corrected behavior. They fail on the pre-fix runtime
(direct clock renderer, literal fallback, mixed speech before terminal,
shallow schema, static 4000-char persona cap) and pass after M1-M3.
"""

import asyncio
import unittest

from config.settings import AppConfig, validate_base_prompt_budget
from core.session import ClientSession
from core.tools.registry import ToolRegistry, ToolValidationError, validate_arguments
from core.tools.builtin.calculator import calculator_descriptor
from core.tools.builtin.time_tool import time_descriptor
from core.turn_events import (
    CompletedEvent,
    ConfirmationDecisionEvent,
    ControlEvent,
    MemoryProposalEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)


class FakeWebSocket:
    request = None

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)
        return True


class FakeASR:
    async def start(self):
        return None

    async def send_audio(self, *args, **kwargs):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, generation):
        return None

    async def stop(self):
        return None


class SessionForTest(ClientSession):
    def _create_asr(self):
        return FakeASR()


class TwoFrameTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        yield b"frame-1"
        yield b"frame-2"


def _assistant_texts(session):
    return [m.content for m in session.dialogue.messages if m.role == "assistant"]


class TimeThenSynthesisLLM:
    """First round emits a time tool; second round AI-phrases the receipt."""

    def __init__(self, second_text="Bây giờ là 07:30, ngày 10/09/2026."):
        self.calls = []
        self.second_text = second_text

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools or [],
                           "detect_end_intent": detect_end_intent, "tool_choice": tool_choice})
        if len(self.calls) == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(call_id="time-1", name="get_current_time", arguments={})
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent(self.second_text, emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class MixedSpeechThenToolLLM:
    """Emits speech before a read-only tool in the same round (must be discarded)."""

    def __init__(self):
        self.calls = []

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools or [],
                           "detect_end_intent": detect_end_intent, "tool_choice": tool_choice})
        if len(self.calls) == 1:
            yield ControlEvent(intent="tool_request")
            yield SpeechSegmentEvent("Để mình kiểm tra nhé.", emotion="neutral")
            yield ToolCallReadyEvent(call_id="time-1", name="get_current_time", arguments={})
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Bây giờ là 07:30.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class NestedFailureLLM:
    """Approve leads to execution failure; synthesis must not claim success."""

    def __init__(self):
        self.calls = []
        self.approved = False

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({"messages": messages})
        if not self.approved:
            self.approved = True
            yield ControlEvent(intent="tool_request")
            yield ConfirmationDecisionEvent(call_id="conf-1", action_id="act-1", decision="approve")
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Thao tác chưa thành công, mình giữ nguyên trạng thái.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class SemanticsRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_a01_clock_uses_ai_synthesis_not_direct_renderer(self):
        config = AppConfig()
        llm = TimeThenSynthesisLLM(second_text="Chào sếp, bây giờ là 07:30.")
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Mấy giờ rồi?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(len(llm.calls), 2)
        texts = _assistant_texts(session)
        self.assertTrue(any("Chào sếp" in t for t in texts))
        # No fixed direct template leaks into the reply.
        self.assertFalse(any(t.startswith("Bây giờ là ") and "ngày" in t and "Chào" not in t
                             for t in texts))

    async def test_a02_nested_confirmation_failure_is_not_false_success(self):
        from core.tools.base import ToolDescriptor
        from core.tools.results import ToolStatus, ToolResult

        async def failing(arguments):
            raise RuntimeError("device offline")

        descriptor = ToolDescriptor(
            name="device_set_volume", description="Set volume",
            input_schema={"type": "object",
                          "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}},
                          "required": ["volume"], "additionalProperties": False},
            handler=failing, read_only=False, idempotent=False,
            requires_confirmation=True,
        )
        config = AppConfig()
        llm = NestedFailureLLM()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        session.tool_registry.register(descriptor)
        session.pending_actions.prepare(
            action_id="act-1", turn_id="t0", tool_name="device_set_volume",
            arguments={"volume": 50}, session_id=session.session_id,
            owner_scope=session._confirmation_owner_scope(), ttl_seconds=60,
        )
        await session._trigger_ai_turn("Đồng ý.")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        texts = _assistant_texts(session)
        joined = " ".join(texts)
        self.assertNotIn("Đã xong", joined)
        self.assertNotIn("Mình đã xử lý", joined)
        self.assertIn("chưa thành công", joined)

    async def test_a03_mixed_speech_is_discarded_before_terminal(self):
        config = AppConfig()
        llm = MixedSpeechThenToolLLM()
        websocket = FakeWebSocket()
        session = SessionForTest(websocket, config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Mấy giờ rồi?")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        texts = _assistant_texts(session)
        # First-round speculative speech never reaches TTS/history.
        self.assertFalse(any("Để mình kiểm tra" in t for t in texts))
        self.assertTrue(any("07:30" in t for t in texts))
        self.assertEqual(len(llm.calls), 2)

    async def test_a09_nested_schema_is_rejected(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                        "nested": {
                            "type": "object",
                            "properties": {"level": {"type": "integer"}},
                            "required": ["level"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["volume"],
                    "additionalProperties": False,
                }
            },
            "required": ["config"],
            "additionalProperties": False,
        }
        validate_arguments(schema, {"config": {"volume": 10, "nested": {"level": 2}}})
        with self.assertRaises(ToolValidationError):
            validate_arguments(schema, {"config": {"volume": "loud", "nested": {"level": 2}}})
        with self.assertRaises(ToolValidationError):
            validate_arguments(schema, {"config": {"volume": 10, "nested": {}}})
        with self.assertRaises(ToolValidationError):
            validate_arguments(schema, {"config": {"volume": 10, "nested": {"level": 2, "extra": 1}}})
        # Boolean must not coerce to integer revision; numeric string rejected.
        mem_schema = {"type": "object",
                      "properties": {"revision": {"type": "integer", "minimum": 1}},
                      "additionalProperties": True}
        with self.assertRaises(ToolValidationError):
            validate_arguments(mem_schema, {"revision": True})
        with self.assertRaises(ToolValidationError):
            validate_arguments(mem_schema, {"revision": "2"})

    async def test_a04_semantic_prompt_is_in_budget(self):
        from core.ai_contract import SEMANTIC_SYSTEM_PROMPT
        config = AppConfig()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), TimeThenSynthesisLLM())
        messages = [{"role": "user", "content": "xin chào"}]
        fitted = session._fit_llm_context(messages, tools=[], detect_end_intent=True)
        budget = session.context_builder.last_budget
        self.assertGreater(budget["system_chars"], len("xin chào"))
        self.assertIn("persona_version", budget)
        self.assertIn("catalog_hash", budget)
        self.assertTrue(SEMANTIC_SYSTEM_PROMPT.strip())

    async def test_a05_persona_over_4000_chars_within_budget_is_allowed(self):
        config = AppConfig()
        long_persona = "Bạn là VeeTee. " + ("Hãy trả lời thân thiện, giữ xưng hô sếp. " * 200)
        self.assertGreater(len(long_persona), 4000)
        # Shared budget validation allows it when inside byte/token caps.
        validate_base_prompt_budget(long_persona, config)
        # Over-budget personas fail loudly instead of silent truncation.
        huge = "x" * (config.llm.base_prompt_max_bytes + 1)
        with self.assertRaises(ValueError):
            validate_base_prompt_budget(huge, config)

    async def test_catalog_truncation_is_explicit_not_silent(self):
        from core.tools.base import ToolDescriptor
        descriptors = [
            ToolDescriptor(name=f"tool_{i:02d}", description=f"tool {i}",
                           input_schema={"type": "object"},
                           handler=lambda arguments: {"ok": True})
            for i in range(20)
        ]
        registry = ToolRegistry(descriptors)
        exposed = registry.openai_tools(limit=16)
        names = [t["function"]["name"] for t in exposed]
        self.assertIn("veetee_tool_catalog", names)
        self.assertTrue(any("tool_19" in t["function"]["description"] for t in exposed
                            if t["function"]["name"] == "veetee_tool_catalog"))


if __name__ == "__main__":
    unittest.main()
