from __future__ import annotations

import json
from time import monotonic

import httpx
import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
)
from veetee_voice_server.providers.cliproxy import CliProxyApiLlmProvider

pytestmark = pytest.mark.asyncio


def context() -> OperationContext:
    return OperationContext(
        "cliproxy-test",
        "cliproxy-test:1",
        1,
        CancellationToken(),
        monotonic() + 5,
    )


async def test_none_reasoning_is_omitted_for_strict_structured_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "reasoning_effort" not in payload
        assert payload["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"{\\\"ok\\\":true}"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CliProxyApiLlmProvider(
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.6-terra",
        api_key="test-key",
        reasoning_effort="none",
        config={
            "reasoningEffort": "none",
            "responseFormat": "json_schema",
        },
        client=client,
    )

    result = await provider.complete_json(
        system_prompt="Return JSON.",
        user_prompt="Return ok.",
        context=context(),
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        schema_transport="json_schema",
    )
    await client.aclose()

    assert result == {"ok": True}


async def test_dynamic_object_schema_uses_json_object_mode() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "reasoning_effort" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"'
                b'{\\\"tool_call\\\":{\\\"arguments\\\":{\\\"level\\\":55}}}"}}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CliProxyApiLlmProvider(
        base_url="http://127.0.0.1:8317/v1",
        model="gpt-5.6-terra",
        api_key="test-key",
        reasoning_effort="none",
        config={
            "reasoningEffort": "none",
            "responseFormat": "json_schema",
        },
        client=client,
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tool_call": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "arguments": {"type": "object"},
                },
                "required": ["arguments"],
            }
        },
        "required": ["tool_call"],
    }

    result = await provider.complete_json(
        system_prompt="Return JSON.",
        user_prompt="Set the level.",
        context=context(),
        schema=schema,
        schema_transport="json_schema",
    )
    await client.aclose()

    assert result == {"tool_call": {"arguments": {"level": 55}}}
