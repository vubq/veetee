from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import ClassVar

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


async def silent_mp3() -> bytes:
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
    return generated


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


def test_edge_tts_voice_controls_map_to_provider_format() -> None:
    assert _edge_percent(1.1) == "+10%"
    assert _edge_percent(0.75) == "-25%"
    assert _edge_pitch(-12) == "-12Hz"


@pytest.mark.asyncio
async def test_edge_tts_decodes_streamed_mp3_to_pcm_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = await silent_mp3()

    class FakeCommunicate:
        arguments: ClassVar[dict[str, object]] = {}

        def __init__(self, *_: object, **kwargs: object) -> None:
            self.__class__.arguments = kwargs

        async def stream(self):  # type: ignore[no-untyped-def]
            middle = len(generated) // 2
            yield {"type": "audio", "data": generated[:middle]}
            yield {"type": "audio", "data": generated[middle:]}

    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    provider = EdgeTtsProvider(voice="vi-VN-HoaiMyNeural", rate=1.5, volume=1.2)
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
    assert FakeCommunicate.arguments["rate"] == "+0%"
    assert FakeCommunicate.arguments["volume"] == "+0%"


@pytest.mark.asyncio
async def test_edge_tts_retries_a_transport_without_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = await silent_mp3()

    class FlakyCommunicate:
        calls = 0
        timeouts: ClassVar[list[tuple[int, int]]] = []

        def __init__(self, *_: object, **kwargs: object) -> None:
            self.__class__.calls += 1
            self.call = self.__class__.calls
            self.__class__.timeouts.append(
                (int(kwargs["connect_timeout"]), int(kwargs["receive_timeout"]))
            )

        async def stream(self):  # type: ignore[no-untyped-def]
            if self.call == 1:
                if False:
                    yield {}
                raise edge_tts.exceptions.NoAudioReceived("fixture")
            yield {"type": "audio", "data": generated}

    monkeypatch.setattr(edge_tts, "Communicate", FlakyCommunicate)
    provider = EdgeTtsProvider(
        voice="vi-VN-HoaiMyNeural",
        config={
            "connectTimeoutSeconds": 3,
            "receiveTimeoutSeconds": 8,
            "maxAttempts": 2,
        },
    )
    context = OperationContext(
        "session-1",
        "session-1:2",
        2,
        CancellationToken(),
        monotonic() + 10,
    )
    chunks = [chunk async for chunk in provider.synthesize("Xin chào", "vi-VN", context)]

    assert FlakyCommunicate.calls == 2
    assert FlakyCommunicate.timeouts == [(2, 7), (2, 7)]
    assert sum(len(chunk.data) for chunk in chunks) > 0


@pytest.mark.asyncio
async def test_edge_tts_first_audio_deadline_is_absolute_across_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = await silent_mp3()

    class MetadataStallCommunicate:
        calls = 0

        def __init__(self, *_: object, **__: object) -> None:
            self.__class__.calls += 1
            self.call = self.__class__.calls

        async def stream(self):  # type: ignore[no-untyped-def]
            if self.call == 1:
                for _ in range(20):
                    await asyncio.sleep(0.01)
                    yield {
                        "type": "WordBoundary",
                        "offset": 0,
                        "duration": 1,
                        "text": "fixture",
                    }
                return
            yield {"type": "audio", "data": generated}

    monkeypatch.setattr(edge_tts, "Communicate", MetadataStallCommunicate)
    provider = EdgeTtsProvider(
        voice="vi-VN-HoaiMyNeural",
        config={"maxAttempts": 2},
    )
    provider._first_audio_timeout = 0.05
    context = OperationContext(
        "session-1",
        "session-1:3",
        3,
        CancellationToken(),
        monotonic() + 5,
    )

    chunks = [chunk async for chunk in provider.synthesize("Xin chào", "vi-VN", context)]

    assert MetadataStallCommunicate.calls == 2
    assert sum(len(chunk.data) for chunk in chunks) > 0
