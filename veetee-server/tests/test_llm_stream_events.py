import asyncio
import json
import unittest

from core.providers.llm.omniroute_groq import OmnirouteGroqLLM, SpeechSegmentSplitter
from core.providers.llm.stream_parser import NativeToolCallAccumulator, SSEDecoder
from core.tools.builtin.time_tool import time_descriptor
from core.turn_events import CompletedEvent, FailedEvent, SpeechSegmentEvent, ToolCallReadyEvent


class LLMStreamEventTests(unittest.TestCase):
    def test_first_long_clause_can_stream_before_sentence_end(self):
        splitter = SpeechSegmentSplitter()
        text = "Mình kiểm tra nhanh thông tin này cho bạn nhé, phần còn lại mình nói ngay sau đó"

        segments = splitter.add_token(text)

        self.assertEqual(segments, ["Mình kiểm tra nhanh thông tin này cho bạn nhé,"])
        self.assertEqual(splitter.buffer, " phần còn lại mình nói ngay sau đó")

    def test_short_intro_clause_is_not_split_too_early(self):
        splitter = SpeechSegmentSplitter()

        segments = splitter.add_token("Ừm, mình đang suy nghĩ thêm để trả lời bạn thật tự nhiên")

        self.assertEqual(segments, [])

    def test_sse_decoder_handles_arbitrary_chunk_boundaries_and_unicode(self):
        decoder = SSEDecoder()
        payload = 'data: {"text":"xin chào"}\n\ndata: [DONE]\n\n'.encode("utf-8")
        events = []
        for byte in payload:
            events.extend(decoder.feed(bytes([byte])))
        self.assertEqual(events, ['{"text":"xin chào"}', "[DONE]"])

    def test_native_tool_call_is_published_only_after_complete_json(self):
        accumulator = NativeToolCallAccumulator()
        accumulator.add_delta([{
            "index": 0,
            "id": "call-1",
            "function": {"name": "calculate", "arguments": '{"expression":"2'},
        }])
        with self.assertRaises(ValueError):
            accumulator.finalize()

        accumulator.add_delta([{
            "index": 0,
            "function": {"arguments": '+3"}'},
        }])
        self.assertEqual(
            accumulator.finalize(),
            [("call-1", "calculate", {"expression": "2+3"})],
        )

    def test_tool_argument_and_call_limits_are_enforced(self):
        accumulator = NativeToolCallAccumulator(max_args_bytes=8, max_calls=1)
        with self.assertRaises(ValueError):
            accumulator.add_delta([{
                "index": 0,
                "function": {"name": "x", "arguments": json.dumps({"x": "123456789"})},
            }])

        accumulator = NativeToolCallAccumulator(max_calls=1)
        accumulator.add_delta([{"index": 0, "function": {"name": "a", "arguments": "{}"}}])
        with self.assertRaises(ValueError):
            accumulator.add_delta([{"index": 1, "function": {"name": "b", "arguments": "{}"}}])

    def test_clean_text_strips_bracket_markers_before_tts(self):
        clean = OmnirouteGroqLLM._clean_text
        self.assertEqual(clean("[surprised] Hả, vừa rồi nghe không rõ lắm á?"),
                         "Hả, vừa rồi nghe không rõ lắm á?")
        self.assertEqual(clean("Ừm [happy] mình hiểu rồi nhé."),
                         "Ừm mình hiểu rồi nhé.")
        self.assertEqual(clean("[end] Tạm biệt nhé."), "Tạm biệt nhé.")
        # Legitimate speech without markers passes through untouched.
        self.assertEqual(clean("Bây giờ là 10 giờ 51 phút rồi nè."),
                         "Bây giờ là 10 giờ 51 phút rồi nè.")

    def test_clean_control_sentence_strips_cached_marker(self):
        clean = OmnirouteGroqLLM._clean_control_sentence
        self.assertEqual(clean("[happy] Khoan, hơi trục trặc chút."),
                         "Khoan, hơi trục trặc chút.")
        self.assertEqual(clean("Ừm, để tôi xử lý lại nha."),
                         "Ừm, để tôi xử lý lại nha.")

    def test_native_tool_call_requires_model_call_id(self):
        accumulator = NativeToolCallAccumulator()
        accumulator.add_delta([{
            "index": 0,
            "function": {"name": "calculate", "arguments": '{"expression":"2+3"}'},
        }])
        with self.assertRaisesRegex(ValueError, "missing.*id"):
            accumulator.finalize()


class LLMStreamCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def _stream_events(self, payload: bytes, *, tools, tool_choice=None, capture=None):
        class Content:
            async def iter_any(self):
                yield payload

        class FakeResponse:
            status = 200
            content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                if capture is not None:
                    capture.update(kwargs)
                return FakeResponse()

        llm = OmnirouteGroqLLM(base_prompt="Bạn là trợ lý tiếng Việt.")

        async def get_fake_session():
            return FakeSession()

        llm._get_http_session = get_fake_session
        return [
            event
            async for event in llm.stream_turn(
                [{"role": "user", "content": "Mấy giờ rồi?"}],
                tools=tools,
                detect_end_intent=False,
                tool_choice=tool_choice,
            )
        ]

    async def test_auto_tool_selection_keeps_context_and_all_tool_schemas(self):
        payload = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-time",'
            '"function":{"name":"get_current_time","arguments":"{}"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            'data: [DONE]\n\n'
        ).encode("utf-8")
        capture = {}
        clock_tool = time_descriptor().as_openai_tool()
        unrelated_tool = {
            "type": "function",
            "function": {
                "name": "other_tool",
                "description": "Other tool",
                "parameters": {"type": "object"},
            },
        }
        events = await self._stream_events(
            payload,
            tools=[clock_tool, unrelated_tool],
            capture=capture,
        )

        request = capture["json"]
        self.assertEqual(request["tool_choice"], "auto")
        self.assertEqual(len(request["tools"]), 2)
        self.assertEqual(
            {tool["function"]["name"] for tool in request["tools"]},
            {"get_current_time", "other_tool"},
        )
        self.assertEqual(request["messages"][-1]["content"], "Mấy giờ rồi?")
        joined = "\n".join(str(message.get("content") or "") for message in request["messages"])
        self.assertIn("Bạn là trợ lý tiếng Việt.", joined)
        self.assertIn("Semantic contract", joined)
        self.assertTrue(any(isinstance(event, ToolCallReadyEvent) for event in events))

    async def test_mixed_content_then_read_only_tool_call_is_allowed(self):
        payload = (
            'data: {"choices":[{"delta":{"content":"Để mình kiểm tra."}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-time",'
            '"function":{"name":"get_current_time","arguments":"{}"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            'data: [DONE]\n\n'
        ).encode("utf-8")

        events = await self._stream_events(payload, tools=[time_descriptor().as_openai_tool()])

        self.assertTrue(any(isinstance(event, SpeechSegmentEvent) for event in events))
        self.assertTrue(any(
            isinstance(event, ToolCallReadyEvent) and event.name == "get_current_time"
            for event in events
        ))
        self.assertTrue(any(isinstance(event, CompletedEvent) for event in events))
        self.assertFalse(any(isinstance(event, FailedEvent) for event in events))

    async def test_mixed_content_then_side_effect_tool_call_is_rejected(self):
        payload = (
            'data: {"choices":[{"delta":{"content":"Để mình làm ngay."}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-volume",'
            '"function":{"name":"device_set_volume","arguments":"{\\"volume\\":50}"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            'data: [DONE]\n\n'
        ).encode("utf-8")
        tools = [{
            "type": "function",
            "function": {
                "name": "device_set_volume",
                "description": "Đặt âm lượng thiết bị.",
                "parameters": {"type": "object"},
            },
        }]

        events = await self._stream_events(payload, tools=tools)

        self.assertFalse(any(isinstance(event, ToolCallReadyEvent) for event in events))
        self.assertTrue(any(
            isinstance(event, FailedEvent)
            and event.error == "action turn emitted content before structured action"
            for event in events
        ))

    async def test_truncated_tool_stream_never_publishes_side_effect_call(self):
        payload = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
            '"function":{"name":"calculate","arguments":"{\\"expression\\":\\"2+3\\"}"}}]}}]}\n\n'
        ).encode("utf-8")

        class Content:
            async def iter_any(self):
                yield payload

        class FakeResponse:
            status = 200
            content = Content()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResponse()

        llm = OmnirouteGroqLLM(base_prompt="Bạn là trợ lý tiếng Việt.")

        async def get_fake_session():
            return FakeSession()

        llm._get_http_session = get_fake_session
        events = [
            event
            async for event in llm.stream_turn(
                [{"role": "user", "content": "Hai cộng ba"}],
                tools=[{"type": "function", "function": {"name": "calculate"}}],
                detect_end_intent=False,
            )
        ]
        self.assertFalse(any(isinstance(event, ToolCallReadyEvent) for event in events))
        self.assertTrue(any(isinstance(event, FailedEvent) for event in events))

    async def test_stream_turn_propagates_cancelled_error_cleanly(self):
        class CancelledContent:
            async def iter_any(self):
                raise asyncio.CancelledError
                yield b""

        class FakeResponse:
            status = 200
            content = CancelledContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResponse()

        llm = OmnirouteGroqLLM(base_prompt="Bạn là trợ lý tiếng Việt.")

        async def get_fake_session():
            return FakeSession()

        llm._get_http_session = get_fake_session

        with self.assertRaises(asyncio.CancelledError):
            async for _ in llm.stream_turn(
                [{"role": "user", "content": "Mấy giờ rồi?"}],
                detect_end_intent=False,
            ):
                pass


if __name__ == "__main__":
    unittest.main()
