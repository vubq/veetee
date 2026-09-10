import asyncio
import json
import unittest

from core.providers.llm.groq_direct import (
    GroqDirectLLM,
    build_targets_from_env,
)
from core.providers.llm.quota import QuotaLedger
from core.providers.llm.router import GroqRouter, RouteTarget
from core.turn_events import (
    CompletedEvent,
    ControlEvent,
    FailedEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)


def sse_data(payload):
    return ("data: " + json.dumps(payload) + "\n\n").encode()


def chat_chunk(content, finish=None):
    delta = {"content": content}
    return sse_data({"choices": [{"delta": delta, "finish_reason": finish}]})


class FakeContent:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, chunks=(), json_body=None, headers=None):
        self.status = status
        self._chunks = list(chunks)
        self._json_body = json_body
        self.headers = headers or {}
        self.released = False
        self.content = FakeContent(self._chunks)

    async def json(self, content_type=None):
        return self._json_body

    async def read(self):
        return b""

    def release(self):
        self.released = True


class FakeSession:
    """Records requests; serves scripted responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "headers": dict(headers or {}),
                              "json": json})
        if not self.responses:
            raise AssertionError("no scripted response left")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, headers=None, timeout=None):
        self.requests.append({"url": url, "method": "GET"})
        return FakeResponse(status=200, json_body={"data": []})


def make_provider(session, groups=None, targets=None, **kwargs):
    groups = groups if groups is not None else {"gA": {"rpm": 100, "tpm": 100000}}
    targets = targets if targets is not None else [
        RouteTarget(alias="A", api_key="kA", quota_group="gA",
                    base_url="https://api.groq.com/openai/v1"),
    ]
    ledger = QuotaLedger(dict(groups))
    router = GroqRouter(targets, ledger)
    params = dict(model="qwen/qwen3.6-27b", temperature=0.0,
                  max_tokens=64, base_prompt="Bạn là VeeTee.",
                  session_factory=lambda: session)
    params.update(kwargs)
    return GroqDirectLLM(router, **params), router, ledger


def collect(stream):
    async def _run():
        return [event async for event in stream]
    return asyncio.get_event_loop().run_until_complete(_run())


class GroqDirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_turn_chat_uses_first_eligible_key(self):
        chunks = [chat_chunk("[continue][happy]Xin chào"),
                  chat_chunk(" bạn nhé.", finish="stop"),
                  b"data: [DONE]\n\n"]
        session = FakeSession([FakeResponse(status=200, chunks=chunks)])
        llm, router, ledger = make_provider(session)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "chào"}], detect_end_intent=True)]
        kinds = [type(e).__name__ for e in events]
        self.assertIn("ControlEvent", kinds)
        self.assertIn("SpeechSegmentEvent", kinds)
        self.assertIn("CompletedEvent", kinds)
        speech = " ".join(e.text for e in events
                          if isinstance(e, SpeechSegmentEvent))
        self.assertNotIn("[happy]", speech)
        self.assertIn("Xin chào", speech)
        # Exactly one upstream request, authorized with key A (not logged).
        self.assertEqual(len(session.requests), 1)
        auth = session.requests[0]["headers"]["Authorization"]
        self.assertEqual(auth, "Bearer kA")
        self.assertTrue(session.requests[0]["url"].startswith(
            "https://api.groq.com/openai/v1"))
        snap = await ledger.snapshot("gA")
        self.assertEqual(snap["in_flight"], 0)

    async def test_429_fails_over_without_sleeping(self):
        bad = FakeResponse(status=429, headers={"retry-after": "30"})
        good_chunks = [chat_chunk("[continue]Ừm."),
                       chat_chunk(" xong.", finish="stop"),
                       b"data: [DONE]\n\n"]
        good = FakeResponse(status=200, chunks=good_chunks)
        session = FakeSession([bad, good])
        targets = [
            RouteTarget(alias="A", api_key="kA", quota_group="gA",
                        base_url="https://x"),
            RouteTarget(alias="B", api_key="kB", quota_group="gB",
                        base_url="https://x"),
        ]
        groups = {"gA": {"rpm": 100, "tpm": 100000},
                  "gB": {"rpm": 100, "tpm": 100000}}
        llm, router, ledger = make_provider(session, groups=groups,
                                            targets=targets)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "hi"}], detect_end_intent=True)]
        self.assertTrue(any(isinstance(e, CompletedEvent) for e in events))
        auths = [r["headers"]["Authorization"] for r in session.requests]
        self.assertEqual(auths, ["Bearer kA", "Bearer kB"])
        snap_a = await ledger.snapshot("gA")
        self.assertGreater(snap_a["cooldown_until"], 0)

    async def test_all_exhausted_yields_failure_without_http(self):
        session = FakeSession([])
        llm, _, _ = make_provider(
            session, groups={"gA": {"rpm": 1, "tpm": 1}})
        # Drain the single request budget first.
        ledger = llm._router._ledger
        rid = await ledger.try_reserve("gA", tokens=1)
        self.assertIsNotNone(rid)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "hi"}])]
        self.assertTrue(any(isinstance(e, FailedEvent) for e in events))
        self.assertEqual(session.requests, [])

    async def test_correct_transcript_non_streaming(self):
        body = {"choices": [{"message": {"content": "Mấy giờ rồi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4}}
        session = FakeSession([FakeResponse(status=200, json_body=body)])
        llm, _, _ = make_provider(session)
        out = await llm.correct_transcript("Mấy giờ rôi")
        self.assertEqual(out, "Mấy giờ rồi")

    async def test_later_clause_bracket_tag_stripped(self):
        chunks = [chat_chunk("[continue][happy]Câu một. [surprised] Ồ thật à?"),
                  chat_chunk(" hết.", finish="stop"),
                  b"data: [DONE]\n\n"]
        session = FakeSession([FakeResponse(status=200, chunks=chunks)])
        llm, _, _ = make_provider(session)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "kể đi"}], detect_end_intent=True)]
        speech = " ".join(e.text for e in events
                          if isinstance(e, SpeechSegmentEvent))
        self.assertNotIn("[surprised]", speech)
        self.assertNotIn("[happy]", speech)

    async def test_stream_without_done_sentinel_still_completes(self):
        # gpt-oss sometimes closes right after finish_reason=stop.
        chunks = [chat_chunk("[continue]Ừm."),
                  chat_chunk(" xong.", finish="stop")]
        session = FakeSession([FakeResponse(status=200, chunks=chunks)])
        llm, _, _ = make_provider(session)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "hi"}], detect_end_intent=True)]
        self.assertTrue(any(isinstance(e, CompletedEvent) for e in events))

    async def test_native_tool_call_end_to_end(self):
        tool_delta = [{"index": 0, "id": "call-1",
                       "function": {"name": "get_current_time",
                                    "arguments": "{}"}}]
        chunks = [
            sse_data({"choices": [{"delta": {"tool_calls": tool_delta},
                                   "finish_reason": None}]}),
            sse_data({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            b"data: [DONE]\n\n",
        ]
        session = FakeSession([FakeResponse(status=200, chunks=chunks)])
        llm, _, _ = make_provider(session)
        events = [e async for e in llm.stream_turn(
            [{"role": "user", "content": "mấy giờ"}],
            tools=[{"type": "function",
                    "function": {"name": "get_current_time"}}])]
        calls = [e for e in events if isinstance(e, ToolCallReadyEvent)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "get_current_time")

    def test_reasoning_params_none_sends_both_keys(self):
        session = FakeSession([])
        llm, _, _ = make_provider(session)
        params = llm._reasoning_params()
        self.assertEqual(params, {"reasoning_format": "hidden",
                                 "reasoning_effort": "none"})

    def test_reasoning_params_low_omits_format(self):
        session = FakeSession([])
        llm, _, _ = make_provider(session, reasoning_effort="low")
        self.assertEqual(llm._reasoning_params(),
                         {"reasoning_effort": "low"})

    def test_engine_for_model_shares_router_and_overrides_effort(self):
        from core.providers.llm.groq_direct import engine_for_model
        session = FakeSession([])
        base, _, _ = make_provider(session, reasoning_effort="none")
        base.set_model_effort_overrides({"openai/gpt-oss-20b": "low"})
        clone = engine_for_model(base, "openai/gpt-oss-20b")
        self.assertIs(clone._router, base._router)
        self.assertEqual(clone.model, "openai/gpt-oss-20b")
        self.assertEqual(clone._reasoning_params(),
                         {"reasoning_effort": "low"})
        self.assertEqual(base.model, "qwen/qwen3.6-27b")

    async def test_cancelled_stream_settles_uncertain_once(self):
        class CancellingSession(FakeSession):
            async def post(self, url, headers=None, json=None, timeout=None):
                self.requests.append({"url": url})
                raise asyncio.CancelledError()

        session = CancellingSession([])
        llm, _, ledger = make_provider(session)
        with self.assertRaises(asyncio.CancelledError):
            async for _ in llm.stream_turn(
                    [{"role": "user", "content": "hi"}]):
                pass
        snap = await ledger.snapshot("gA")
        self.assertEqual(snap["in_flight"], 0)

    def test_build_targets_from_env_flexible_count(self):
        import os
        backup = dict(os.environ)
        try:
            for key in list(os.environ):
                if key.startswith("GROQ_API_KEY_"):
                    del os.environ[key]
            os.environ["GROQ_API_KEY_X1"] = "s1"
            os.environ["GROQ_API_KEY_X2"] = "s2"
            os.environ["GROQ_API_KEY_X3"] = ""
            targets = build_targets_from_env(None)
            self.assertEqual([t.alias for t in targets], ["X1", "X2"])
            self.assertEqual(targets[0].quota_group, "gX1")
            self.assertNotIn("s1", str([(t.alias, t.quota_group) for t in targets]))
        finally:
            os.environ.clear()
            os.environ.update(backup)

    def test_explicit_pool_missing_env_disables(self):
        import os
        backup = dict(os.environ)
        try:
            for key in list(os.environ):
                if key.startswith("GROQ_API_KEY_"):
                    del os.environ[key]
            os.environ["GROQ_API_KEY_A"] = "sA"
            targets = build_targets_from_env([
                {"id": "A", "api_key_env": "GROQ_API_KEY_A"},
                {"id": "B", "api_key_env": "GROQ_API_KEY_MISSING"},
            ])
            self.assertEqual([t.alias for t in targets], ["A"])
        finally:
            os.environ.clear()
            os.environ.update(backup)


if __name__ == "__main__":
    unittest.main()
