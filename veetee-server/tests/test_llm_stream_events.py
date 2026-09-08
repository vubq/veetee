import asyncio
import json
import unittest

from core.providers.llm.omniroute_groq import OmnirouteGroqLLM
from core.providers.llm.stream_parser import NativeToolCallAccumulator, SSEDecoder
from core.turn_events import FailedEvent, ToolCallReadyEvent


class LLMStreamEventTests(unittest.TestCase):
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

    def test_native_tool_call_requires_model_call_id(self):
        accumulator = NativeToolCallAccumulator()
        accumulator.add_delta([{
            "index": 0,
            "function": {"name": "calculate", "arguments": '{"expression":"2+3"}'},
        }])
        with self.assertRaisesRegex(ValueError, "missing.*id"):
            accumulator.finalize()


class LLMStreamCancellationTests(unittest.IsolatedAsyncioTestCase):
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
