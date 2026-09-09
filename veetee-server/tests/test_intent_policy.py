import asyncio
import unittest

from config.settings import AppConfig
from core.intent import PendingActionStore
from core.session import ClientSession
from core.tools.base import ToolDescriptor
from core.turn_events import (
    CompletedEvent,
    ConfirmationDecisionEvent,
    ControlEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)


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


class ScriptedLLM:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({
            "messages": [dict(item) for item in messages],
            "tools": list(tools or []),
            "detect_end_intent": detect_end_intent,
            "tool_choice": tool_choice,
        })
        script = self.scripts[len(self.calls) - 1] if len(self.calls) <= len(self.scripts) else [
            ControlEvent(),
            SpeechSegmentEvent("Mình nghe đây."),
            CompletedEvent(),
        ]
        for event in script:
            yield event


def tool_request(call_id="call-confirm-1", level=7):
    return [
        ControlEvent(intent="tool_request"),
        ToolCallReadyEvent(call_id=call_id, name="mutate_test", arguments={"level": level}),
        CompletedEvent(finish_reason="tool_calls"),
    ]


def speech(text):
    return [ControlEvent(), SpeechSegmentEvent(text), CompletedEvent(finish_reason="stop")]


class IntentPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_pending_action_resolve_requires_exact_binding_and_keeps_clarify(self):
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

        self.assertEqual(
            store.resolve(
                action_id="a1",
                decision="approve",
                session_id="s2",
                owner_scope="owner:o1",
                now=11.0,
            )[0],
            "mismatch",
        )
        self.assertEqual(
            store.resolve(
                action_id="wrong",
                decision="approve",
                session_id="s1",
                owner_scope="owner:o1",
                now=11.0,
            )[0],
            "mismatch",
        )

        decision, matched = store.resolve(
            action_id="a1",
            decision="clarify",
            session_id="s1",
            owner_scope="owner:o1",
            now=11.0,
        )
        self.assertEqual(decision, "clarify")
        self.assertEqual(matched.action_id, "a1")
        self.assertIsNotNone(store.peek(now=11.0))

        decision, matched = store.resolve(
            action_id="a1",
            decision="approve",
            session_id="s1",
            owner_scope="owner:o1",
            now=11.0,
        )
        self.assertEqual(decision, "approve")
        self.assertEqual(matched.action_id, "a1")
        self.assertIsNone(store.peek(now=11.0))

    def test_pending_action_ttl_and_argument_change_remain_deterministic_guards(self):
        store = PendingActionStore()
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

    async def test_side_effect_executes_only_after_ai_emits_matching_confirmation_event(self):
        executed = []

        async def mutate(arguments):
            executed.append(dict(arguments))
            return {"ok": True, "level": arguments["level"]}

        llm = ScriptedLLM([
            tool_request(),
            speech("Bạn xác nhận mức 7 nhé?"),
            [
                ControlEvent(),
                ConfirmationDecisionEvent(
                    call_id="decision-1",
                    action_id="call-confirm-1",
                    decision="approve",
                ),
                CompletedEvent(finish_reason="tool_calls"),
            ],
            speech("Đã thực hiện theo xác nhận của bạn."),
            speech("Mình nghe đây."),
        ])
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
        ))

        await session._trigger_ai_turn("Đặt mức 7")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [])
        self.assertEqual(session.pending_actions.peek().action_id, "call-confirm-1")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(llm.calls[1]["tool_choice"], "none")

        await session._trigger_ai_turn("Ừ, đặt như vậy đi")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [{"level": 7}])
        self.assertIsNone(session.pending_actions.peek())
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(llm.calls[3]["tool_choice"], "none")

        await session._trigger_ai_turn("được!")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(executed, [{"level": 7}])
        self.assertEqual(len(llm.calls), 5)

    async def test_unrelated_or_ambiguous_text_does_not_clear_pending_without_ai_decision(self):
        llm = ScriptedLLM([
            tool_request(),
            speech("Bạn xác nhận mức 7 nhé?"),
            speech("Mình trả lời câu khác trước."),
            speech("Mình nghe bạn nói ừm."),
        ])
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
        self.assertIsNotNone(session.pending_actions.peek())

        await session._trigger_ai_turn("ừm")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertIsNotNone(session.pending_actions.peek())

    async def test_read_only_tool_executes_without_confirmation(self):
        executed = []

        async def read_tool(arguments):
            executed.append(True)
            return "ok"

        llm = ScriptedLLM([
            [
                ControlEvent(intent="tool_request"),
                ToolCallReadyEvent(call_id="read-1", name="read_test", arguments={}),
                CompletedEvent(finish_reason="tool_calls"),
            ],
            speech("Đây là trạng thái hiện tại."),
        ])
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


if __name__ == "__main__":
    unittest.main()
