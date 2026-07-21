from __future__ import annotations

import json

import httpx
import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
)
from veetee_voice_server.conversation.types import (
    ConversationPlan,
    DialogueAct,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.contracts import (
    LlmStreamDone,
    LlmTextDelta,
    LlmToolCallFragment,
)
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider

pytestmark = pytest.mark.asyncio


def context() -> OperationContext:
    import time

    return OperationContext(
        "session-1", "session-1:1", 1, CancellationToken(), time.monotonic() + 5
    )


def request() -> object:
    return type(
        "Request",
        (),
        {
            "transcript": Transcript("Xin chao", "vi-VN"),
            "plan": ConversationPlan(
                PlanAction.RESPOND, DialogueAct.QUESTION, "vi-VN", "fixture.question", True
            ),
            "tool_result": None,
        },
    )()


async def test_stream_accepts_terminal_finish_without_done_marker_and_filters_reasoning() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers.get("authorization") == "Bearer test-key"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"Xin chao."}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NineRouterLlmProvider(
        base_url="http://router/v1",
        model="cx/gpt-5.4-mini",
        api_key="test-key",
        client=client,
    )
    events = [event async for event in provider.stream(request(), context())]  # type: ignore[arg-type]
    await client.aclose()

    assert events == [LlmTextDelta("Xin chao."), LlmStreamDone("stop")]


async def test_stream_preserves_tool_call_fragments_for_the_tool_broker() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        events = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "self.audio_speaker.set_volume",
                                        "arguments": '{"volume":',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"function": {"arguments": "55}"}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(f"data: {json.dumps(event)}\n\n".encode() for event in events),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NineRouterLlmProvider(base_url="http://router/v1", model="test", client=client)
    events = [event async for event in provider.stream(request(), context())]  # type: ignore[arg-type]
    await client.aclose()

    assert events[0] == LlmToolCallFragment("call-1", "self.audio_speaker.set_volume", '{"volume":')
    assert events[1] == LlmToolCallFragment(None, None, "55}")
    assert events[-1] == LlmStreamDone("tool_calls")


async def test_structured_completion_parses_json_object() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"action":"respond","intent":"test"}'}}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = NineRouterLlmProvider(base_url="http://router/v1", model="test", client=client)
    value = await provider.complete_json(
        system_prompt="Return JSON", user_prompt="Xin chào", context=context()
    )
    await client.aclose()
    assert value == {"action": "respond", "intent": "test"}
