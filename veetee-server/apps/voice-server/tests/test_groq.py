from __future__ import annotations

import asyncio
import json
from time import monotonic

import edge_tts
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
from veetee_voice_server.providers.edge_tts import EdgeTtsProvider, _edge_percent, _edge_pitch
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
    )
    await client.aclose()

    assert value == {"accepted": True}
    assert observed["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in observed


def test_edge_tts_voice_controls_map_to_provider_format() -> None:
    assert _edge_percent(1.1) == "+10%"
    assert _edge_percent(0.75) == "-25%"
    assert _edge_pitch(-12) == "-12Hz"


@pytest.mark.asyncio
async def test_edge_tts_decodes_streamed_mp3_to_pcm_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        "0.05",
        "-f",
        "mp3",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    generated, error = await process.communicate()
    assert process.returncode == 0, error.decode(errors="replace")

    class FakeCommunicate:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def stream(self):  # type: ignore[no-untyped-def]
            middle = len(generated) // 2
            yield {"type": "audio", "data": generated[:middle]}
            yield {"type": "audio", "data": generated[middle:]}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    provider = EdgeTtsProvider(voice="vi-VN-HoaiMyNeural")
    context = OperationContext(
        "session-1",
        "session-1:1",
        1,
        CancellationToken(),
        monotonic() + 5,
    )
    chunks = [chunk async for chunk in provider.synthesize("Xin chào", "vi-VN", context)]
    assert chunks[-1].final is True
    assert chunks[-1].data == b""
    assert sum(len(chunk.data) for chunk in chunks) > 0
    assert all(len(chunk.data) % 2 == 0 for chunk in chunks)
