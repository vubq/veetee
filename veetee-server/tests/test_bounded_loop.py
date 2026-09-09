"""M4 regression: bounded multi-round loop, structured history, catalog."""

import asyncio
import unittest

from config.settings import AppConfig
from core.session import ClientSession
from core.turn_events import CompletedEvent, ControlEvent, SpeechSegmentEvent, ToolCallReadyEvent


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
        yield b"f1"
        yield b"f2"


class ChatLLM:
    def __init__(self):
        self.calls = []

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({"tools": tools or [], "tool_choice": tool_choice})
        yield ControlEvent()
        yield SpeechSegmentEvent("Xin chào.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class ChainLLM:
    """Round1: tool A. Round2 (allowed tools): tool B. Round3: speech."""

    def __init__(self):
        self.calls = []

    async def stream_turn(self, messages, *, tools=None, detect_end_intent=True, tool_choice=None):
        self.calls.append({"tools": tools or [], "tool_choice": tool_choice})
        if len(self.calls) == 1:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(call_id="a-1", name="get_current_time", arguments={})
            yield CompletedEvent(finish_reason="tool_calls")
            return
        if len(self.calls) == 2:
            yield ControlEvent(intent="tool_request")
            yield ToolCallReadyEvent(call_id="b-1", name="calculate",
                                     arguments={"expression": "2+3"})
            yield CompletedEvent(finish_reason="tool_calls")
            return
        yield ControlEvent()
        yield SpeechSegmentEvent("Xong cả hai.", emotion="neutral")
        yield CompletedEvent(finish_reason="stop")


class BoundedLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_followup_stops_before_round_ceiling(self):
        from tests.test_turn_lifecycle import NativeToolLLM
        config = AppConfig()
        config.tools.max_llm_rounds_per_turn = 4
        llm = NativeToolLLM()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Hai cộng ba?")
        await asyncio.wait_for(session.current_turn_task, timeout=1)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 2)
        self.assertTrue(any(m.role == "assistant" and "5" in m.content
                            for m in session.dialogue.messages))

    async def test_chat_stays_single_round_without_classifier(self):
        config = AppConfig()
        llm = ChatLLM()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Xin chào")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 1)

    async def test_dependent_chain_runs_with_max_3_rounds(self):
        config = AppConfig()
        config.tools.max_llm_rounds_per_turn = 3
        llm = ChainLLM()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Cho tôi giờ hiện tại rồi cộng 2+3.")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(session.turn_metrics.latest_summary()["llm_rounds"], 3)
        assistants = [m.content for m in session.dialogue.messages if m.role == "assistant"]
        self.assertTrue(any("Xong cả hai" in t for t in assistants))
        # Follow-up understands the prior receipt: history keeps both.
        systems = [m.content for m in session.dialogue.messages if m.role == "system"]
        self.assertTrue(any("a-1" in s for s in systems))
        self.assertTrue(any("b-1" in s for s in systems))

    async def test_chain_is_bounded_when_max_is_2(self):
        config = AppConfig()
        config.tools.max_llm_rounds_per_turn = 2
        llm = ChainLLM()
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Cho tôi giờ hiện tại rồi cộng 2+3.")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        # Second round is final/speech-only so the chained B tool is rejected.
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(session.turn_metrics.latest_summary()["tool_calls"], 1)

    async def test_independent_reads_overlap(self):
        import time as _time
        from core.tools.base import ToolDescriptor
        from core.tools.executor import ToolExecutor
        from core.tools.registry import ToolRegistry

        started = {}

        async def slow(name, delay=0.05):
            started[name] = _time.perf_counter()
            await asyncio.sleep(delay)
            return {"name": name}

        descriptors = [
            ToolDescriptor(name="read_a", description="r", input_schema={"type": "object"},
                           handler=lambda arguments, _n="a": slow(_n), read_only=True,
                           concurrency_group="group_a"),
            ToolDescriptor(name="read_b", description="r", input_schema={"type": "object"},
                           handler=lambda arguments, _n="b": slow(_n), read_only=True,
                           concurrency_group="group_b"),
        ]
        # Note: lambda returns coroutine; executor awaits it.
        executor = ToolExecutor(ToolRegistry(descriptors), max_parallel_read_only=2)
        begin = _time.perf_counter()
        left, right = await asyncio.gather(
            executor.execute("call-a", "read_a", {}, turn_id="t1"),
            executor.execute("call-b", "read_b", {}, turn_id="t1"),
        )
        elapsed = _time.perf_counter() - begin
        self.assertTrue(left.ok and right.ok)
        self.assertLess(elapsed, 0.09)
        self.assertLess(abs(started["a"] - started["b"]), 0.03)

    async def test_structured_history_preserves_receipts(self):
        config = AppConfig()
        llm = ChainLLM()
        config.tools.max_llm_rounds_per_turn = 3
        session = SessionForTest(FakeWebSocket(), config, TwoFrameTTS(), llm)
        await session._trigger_ai_turn("Chuỗi.")
        await asyncio.wait_for(session.current_turn_task, timeout=1.0)
        self.assertTrue(session.dialogue.structured_turns)
        last = session.dialogue.structured_turns[-1]
        self.assertTrue(last.receipts)
        self.assertIn(last.playback, {"sent", "generated"})


if __name__ == "__main__":
    unittest.main()
