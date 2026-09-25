import json
import unittest

from core.providers.llm.groq_direct import GroqDirectLLM, SpeechSegmentSplitter
from core.providers.llm.stream_parser import NativeToolCallAccumulator, SSEDecoder


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

    def test_clean_text_strips_internal_markers_before_tts(self):
        clean = GroqDirectLLM._clean_text
        self.assertEqual(clean("[surprised] Hả, vừa rồi nghe không rõ lắm á?"),
                         "Hả, vừa rồi nghe không rõ lắm á?")
        self.assertEqual(clean("Ừm [happy] mình hiểu rồi nhé."),
                         "Ừm mình hiểu rồi nhé.")
        self.assertEqual(clean("[end] Tạm biệt nhé."), "Tạm biệt nhé.")
        self.assertEqual(clean("Mã [ABC] vẫn phải được đọc nguyên vẹn."),
                         "Mã [ABC] vẫn phải được đọc nguyên vẹn.")

    def test_native_tool_call_requires_model_call_id(self):
        accumulator = NativeToolCallAccumulator()
        accumulator.add_delta([{
            "index": 0,
            "function": {"name": "calculate", "arguments": '{"expression":"2+3"}'},
        }])
        with self.assertRaisesRegex(ValueError, "missing.*id"):
            accumulator.finalize()


if __name__ == "__main__":
    unittest.main()
