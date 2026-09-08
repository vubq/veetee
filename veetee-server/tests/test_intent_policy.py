import asyncio
import unittest

from config.settings import AppConfig
from core.intent import PendingActionStore, confirmation_value
from core.session import ClientSession
from core.tools.base import ToolDescriptor
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent, ToolCallReadyEvent


class FakeWebSocket:
    request = None

    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class FakeASR:
    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_audio(self, pcm_bytes, capture_generation=None):
        return None

    async def finalize(self):
        return None

    def invalidate_capture(self, capture_generation):
        return None


class SessionForTest(ClientSession):
    def _create_asr(self):
        return FakeASR()


class TwoFrameTTS:
    async def stream_sentence_to_opus(self, text, cancel_event=None):
        yield b"frame-1"
        yield b"frame-2"


class ToolThenChatLLM:
    def __init__(self, tool_name, arguments):
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = 0

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls += 1
        if self.calls == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(
                call_id="call-confirm-1",
                name=self.tool_name,
                arguments=self.arguments,
            )
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Mình nghe đây.")
        yield CompletedEvent()


class IntentPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_short_confirmation_parser_is_strict(self):
        self.assertIs(confirmation_value("ừ"), True)
        self.assertIs(confirmation_value("Không nhé."), False)
        self.assertIsNone(confirmation_value("ừ nhưng đặt thành 30 nhé"))
        self.assertIsNone(confirmation_value("tôi nói là 'ừ' lúc nãy"))

    def test_pending_action_ttl_session_owner_and_args_binding(self):
        store = PendingActionStore()
        pending = store.prepare(
            action_id="a1",
            tool_name="device_set_volume",
            arguments={"volume": 40},
            session_id="s1",
            owner_scope="owner:o1",
            ttl_seconds=5,
            now=10.0,
        )
        self.assertEqual(pending.tool_name, "device_set_volume")
        self.assertEqual(store.consume_confirmation(
            "ừ", session_id="s2", owner_scope="owner:o1", now=11.0
        ), (None, None))
        self.assertEqual(store.consume_confirmation(
            "ừ", session_id="s1", owner_scope="owner:o2", now=11.0
        ), (None, None))
        decision, matched = store.consume_confirmation(
            "ừ", session_id="s1", owner_scope="owner:o1", now=11.0
        )
        self.assertIs(decision, True)
        self.assertEqual(matched.action_id, "a1")
        self.assertIsNone(store.peek(now=11.0))

        store.prepare(
            action_id="a2",
            tool_name="device_set_volume",
            arguments={"volume": 40},
            session_id="s1",
            owner_scope="owner:o1",
            ttl_seconds=5,
            now=20.0,
        )
        self.assertTrue(store.invalidate_if_changed(
            tool_name="device_set_volume", arguments={"volume": 50}, now=20.0
        ))
        self.assertIsNone(store.peek(now=20.0))

        store.prepare(
            action_id="a3",
            tool_name="device_set_volume",
            arguments={"volume": 40},
            session_id="s1",
            owner_scope="owner:o1",
            ttl_seconds=5,
            now=30.0,
        )
        self.assertIsNone(store.peek(now=35.01))

    async def test_side_effect_executes_only_after_matching_confirmation_without_second_llm_call(self):
        executed = []

        async def mutate(arguments):
            executed.append(dict(arguments))
            return {"ok": True, "level": arguments["level"]}

        llm = ToolThenChatLLM("mutate_test", {"level": 7})
        session = SessionForTest(FakeWebSocket(), AppConfig(), TwoFrameTTS(), llm)
        session.tool_registry.register(ToolDescriptor(
            name="mutate_test",
            description="test mutation",
            input_schema={
                "type": "object",
                "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 10}},
                "required": ["level"],
                "additionalProperties": False,
            },
            handler=mutate,
            read_only=False,
            idempotent=True,
            requires_confirmation=True,
            confirmation_prompt="Xác nhận đặt mức {level}?",
        ))

        await session._trigger_ai_turn("Đặt mức 7")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [])
        pending = session.pending_actions.peek()
        self.assertIsNotNone(pending)
        self.assertNotEqual(pending.turn_id, "legacy")
        self.assertEqual(llm.calls, 1)

        await session._trigger_ai_turn("ừ")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [{"level": 7}])
        self.assertEqual(llm.calls, 1)
        self.assertIsNone(session.pending_actions.peek())

        # With no pending action, a later standalone confirmation goes through normal
        # chat and cannot replay the prior side effect.
        await session._trigger_ai_turn("được")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [{"level": 7}])
        self.assertEqual(llm.calls, 2)

        # A freshly prepared action can still accept the same short
        # confirmation text used for an earlier action exactly once.
        session.pending_actions.prepare(
            action_id="call-confirm-2",
            tool_name="mutate_test",
            arguments={"level": 8},
            session_id=session.session_id,
            owner_scope=session._confirmation_owner_scope(),
            ttl_seconds=10,
        )
        await session._trigger_ai_turn("ừ")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [{"level": 7}, {"level": 8}])
        self.assertEqual(llm.calls, 2)
        self.assertIsNone(session.pending_actions.peek())

    async def test_read_only_tool_executes_without_confirmation(self):
        executed = []

        async def read_tool(arguments):
            executed.append(True)
            return "ok"

        llm = ToolThenChatLLM("read_test", {})
        session = SessionForTest(FakeWebSocket(), AppConfig(), TwoFrameTTS(), llm)
        session.tool_registry.register(ToolDescriptor(
            name="read_test",
            description="test read",
            input_schema={"type": "object", "additionalProperties": False},
            handler=read_tool,
            read_only=True,
            requires_confirmation=True,
        ))

        await session._trigger_ai_turn("Đọc trạng thái")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [True])
        self.assertIsNone(session.pending_actions.peek())

    async def test_unrelated_turn_invalidates_pending_action(self):
        llm = ToolThenChatLLM("mutate_test", {"level": 7})
        session = SessionForTest(FakeWebSocket(), AppConfig(), TwoFrameTTS(), llm)
        session.tool_registry.register(ToolDescriptor(
            name="mutate_test",
            description="test mutation",
            input_schema={
                "type": "object",
                "properties": {"level": {"type": "integer"}},
                "required": ["level"],
                "additionalProperties": False,
            },
            handler=lambda arguments: None,
            read_only=False,
            requires_confirmation=True,
        ))

        await session._trigger_ai_turn("Đặt mức 7")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertIsNotNone(session.pending_actions.peek())

        await session._trigger_ai_turn("thôi nói chuyện khác")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertIsNone(session.pending_actions.peek())


if __name__ == "__main__":
    unittest.main()
