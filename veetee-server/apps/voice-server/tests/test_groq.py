from __future__ import annotations

import json
from time import monotonic

import httpx
import pytest

from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.conversation.types import (
    ConversationPlan,
    DialogueAct,
    PlanAction,
    Transcript,
)
from veetee_voice_server.providers.contracts import LlmRequest
from veetee_voice_server.providers.groq import GroqCloudLlmProvider


def test_groq_payload_uses_cloud_specific_parameters_without_changing_context() -> None:
    provider = GroqCloudLlmProvider(
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key="not-used-in-test",
        config={
            "temperature": 0.2,
            "topP": 0.95,
            "maxCompletionTokens": 1024,
            "serviceTier": "on_demand",
            "reasoningEffort": "none",
            "parallelToolCalls": True,
        },
    )
    payload = provider._payload(
        LlmRequest(
            transcript=Transcript("Hôm nay là thứ mấy?", "vi-VN"),
            plan=ConversationPlan(
                PlanAction.RESPOND,
                DialogueAct.QUESTION,
                "vi-VN",
                "calendar.day_of_week",
                True,
            ),
        )
    )
    assert payload["model"] == "llama-3.3-70b-versatile"
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.95
    assert payload["service_tier"] == "on_demand"
    assert payload["parallel_tool_calls"] is True
    assert "reasoning_effort" not in payload
    assert "metadata" not in payload
    assert json.loads(payload["messages"][-1]["content"].split("Turn metadata (JSON): ", 1)[1])[
        "dialogue_act"
    ] == "question"
    assert provider._config["responseFormat"] == "json_object"
    assert "reasoningEffort" not in provider._config


def test_groq_reasoning_is_preserved_for_supported_model_families() -> None:
    provider = GroqCloudLlmProvider(
        base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-20b",
        config={"reasoningEffort": "medium"},
    )
    payload = provider._payload(
        LlmRequest(
            transcript=Transcript("Giải thích ngắn gọn.", "vi-VN"),
            plan=ConversationPlan(
                PlanAction.RESPOND,
                DialogueAct.QUESTION,
                "vi-VN",
                "explain",
                True,
            ),
        )
    )
    assert payload["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_groq_structured_gate_uses_streaming_json_object_mode() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"{\\"accepted\\":true}"},'
                b'"finish_reason":"stop"}]}\n\n'
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GroqCloudLlmProvider(
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        config={"responseFormat": "auto", "reasoningEffort": "none"},
        client=client,
    )
    value = await provider.complete_json(
        system_prompt="Return JSON",
        user_prompt="Xin chào",
        context=OperationContext(
            "session-1",
            "session-1:1",
            1,
            CancellationToken(),
            monotonic() + 5,
        ),
        schema={"type": "object", "properties": {"accepted": {"type": "boolean"}}},
        schema_transport="json_schema",
        max_output_tokens=256,
    )
    await client.aclose()

    assert value == {"accepted": True}
    assert observed["response_format"] == {"type": "json_object"}
    assert observed["max_completion_tokens"] == 256
    assert "reasoning_effort" not in observed
