"""Deterministic contract tests for the M2.3 OmniRoute Groq adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.pipeline.llm import (
    ChatMessage,
    CircuitBreaker,
    CircuitState,
    LLMCircuitOpenError,
    LLMCompletedEvent,
    LLMConnectTimeoutError,
    LLMEmptyResponseError,
    LLMFirstTokenTimeoutError,
    LLMMalformedStreamError,
    LLMOversizedStreamError,
    LLMProviderAuthError,
    LLMProviderRateLimitError,
    LLMProviderUnavailableError,
    LLMTextDeltaEvent,
    LLMToolCallDeltaEvent,
    LLMTotalTimeoutError,
    LLMUsageEvent,
    OmniRouteLLMAdapter,
    OmniRouteLLMConfig,
    OmniRouteLLMRuntime,
)
from veetee_server.pipeline.llm.omniroute import SSEDecoder, parse_retry_after


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], gate: asyncio.Event | None = None) -> None:
        self.chunks = chunks
        self.gate = gate
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.gate is not None:
            await self.gate.wait()
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _sse(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return f"data: {data}\n\n".encode()


def _runtime(
    handler: Any,
    **config_overrides: Any,
) -> OmniRouteLLMRuntime:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://omniroute.test/v1",
    )
    config = OmniRouteLLMConfig(api_key="test-key", **config_overrides)
    return OmniRouteLLMRuntime(config, http_client=client)


async def _events(runtime: OmniRouteLLMRuntime) -> list[Any]:
    await runtime.startup()
    adapter = runtime.create_adapter()
    return [
        event
        async for event in adapter.generate_stream(
            [ChatMessage(role="user", content="Xin chào")]
        )
    ]


@pytest.mark.asyncio
async def test_fragmented_text_done_usage_and_reasoning_is_hidden() -> None:
    content = _sse(
        {
            "choices": [
                {"delta": {"reasoning_content": "bí mật", "content": "Xin "}}
            ]
        }
    )
    usage = _sse(
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
    )
    raw = content + usage + _sse("[DONE]") + b"data: not-consumed\n\n"
    stream = ChunkStream([raw[:7], raw[7:19], raw[19:]])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["reasoning_effort"] == "low"
        return httpx.Response(200, stream=stream)

    runtime = _runtime(handler)
    events = await _events(runtime)
    assert [event.delta for event in events if isinstance(event, LLMTextDeltaEvent)] == [
        "Xin "
    ]
    assert len([event for event in events if isinstance(event, LLMUsageEvent)]) == 1
    completed = next(event for event in events if isinstance(event, LLMCompletedEvent))
    assert completed.text == "Xin "
    assert completed.usage is not None and completed.usage.total_tokens == 5
    assert stream.closed


def test_sse_decoder_handles_fragmented_bom_cr_and_multiline_data() -> None:
    decoder = SSEDecoder(1024)
    raw = b'\xef\xbb\xbfdata: {"choices":\rdata: []}\r\r'
    events: list[str] = []
    for chunk in (raw[:1], raw[1:2], raw[2:17], raw[17:]):
        events.extend(decoder.feed_bytes(chunk))
    events.extend(decoder.finish())
    assert events == ['{"choices":\n[]}']


@pytest.mark.asyncio
async def test_partial_stream_without_done_fails_closed() -> None:
    runtime = _runtime(
        lambda request: httpx.Response(
            200,
            stream=ChunkStream(
                [_sse({"choices": [{"delta": {"content": "bị cắt"}}]})]
            ),
        )
    )
    await runtime.startup()
    with pytest.raises(LLMMalformedStreamError, match=r"before \[DONE\]"):
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="x")]
        ):
            pass


@pytest.mark.asyncio
async def test_unframed_done_at_eof_fails_closed() -> None:
    payload = _sse({"choices": [{"delta": {"content": "x"}}]}) + b"data: [DONE]"
    runtime = _runtime(
        lambda request: httpx.Response(200, stream=ChunkStream([payload]))
    )
    await runtime.startup()
    with pytest.raises(LLMMalformedStreamError, match=r"before \[DONE\]"):
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="x")]
        ):
            pass


@pytest.mark.asyncio
async def test_multiline_fragmented_tool_call_merges_by_index() -> None:
    chunks = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_",
                                    "function": {"name": "get_", "arguments": '{"city":'},
                                }
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "1",
                                    "function": {"name": "weather", "arguments": '"Đà Nẵng"}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ),
        _sse("[DONE]"),
    ]
    runtime = _runtime(lambda request: httpx.Response(200, stream=ChunkStream(chunks)))
    events = await _events(runtime)
    assert len([event for event in events if isinstance(event, LLMToolCallDeltaEvent)]) == 2
    completed = next(event for event in events if isinstance(event, LLMCompletedEvent))
    assert completed.text == ""
    assert completed.tool_calls[0].id == "call_1"
    assert completed.tool_calls[0].name == "get_weather"
    assert completed.tool_calls[0].parsed_arguments == {"city": "Đà Nẵng"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, LLMProviderAuthError),
        (403, LLMProviderAuthError),
        (429, LLMProviderRateLimitError),
        (503, LLMProviderUnavailableError),
    ],
)
async def test_http_error_normalization(status: int, error: type[Exception]) -> None:
    runtime = _runtime(
        lambda request: httpx.Response(
            status,
            headers={"Retry-After": "12"},
            content=b"api_key=must-not-leak",
        )
    )
    await runtime.startup()
    with pytest.raises(error) as caught:
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="private prompt")]
        ):
            pass
    assert "must-not-leak" not in str(caught.value)
    if isinstance(caught.value, LLMProviderRateLimitError):
        assert caught.value.retry_after == 12.0


@pytest.mark.asyncio
async def test_malformed_oversized_and_empty_streams() -> None:
    cases = [
        ([b"data: {bad}\n\n"], LLMMalformedStreamError, {}),
        ([b"x" * 20], LLMOversizedStreamError, {"max_response_bytes": 10}),
        ([_sse("[DONE]")], LLMEmptyResponseError, {}),
    ]
    for chunks, expected, overrides in cases:
        runtime = _runtime(
            lambda request, chunks=chunks: httpx.Response(200, stream=ChunkStream(chunks)),
            **overrides,
        )
        await runtime.startup()
        with pytest.raises(expected):
            async for _ in runtime.create_adapter().generate_stream(
                [ChatMessage(role="user", content="x")]
            ):
                pass


@pytest.mark.asyncio
async def test_invalid_usage_and_tool_arguments_are_typed_malformed_errors() -> None:
    payloads = [
        _sse({"choices": [], "usage": {"total_tokens": "not-a-number"}}),
        _sse({"choices": [{"delta": {"content": "x"}}], "usage": "bad"}),
        _sse({"choices": [{"delta": {"tool_calls": {"index": 0}}}]}),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "weather", "arguments": "{"},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        + _sse("[DONE]"),
    ]
    for payload in payloads:
        runtime = _runtime(
            lambda request, payload=payload: httpx.Response(
                200, stream=ChunkStream([payload])
            )
        )
        await runtime.startup()
        with pytest.raises(LLMMalformedStreamError):
            async for _ in runtime.create_adapter().generate_stream(
                [ChatMessage(role="user", content="x")]
            ):
                pass


@pytest.mark.asyncio
async def test_generic_transport_error_is_typed_and_opens_circuit() -> None:
    def write_error(request: httpx.Request) -> httpx.Response:
        raise httpx.WriteError("failed", request=request)

    runtime = _runtime(write_error, circuit_breaker_failure_threshold=1)
    await runtime.startup()
    adapter = runtime.create_adapter()
    with pytest.raises(LLMProviderUnavailableError):
        async for _ in adapter.generate_stream([ChatMessage(role="user", content="x")]):
            pass
    assert adapter.circuit_breaker.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_connect_and_first_output_timeout_close_stream() -> None:
    def connect_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    runtime = _runtime(connect_timeout)
    await runtime.startup()
    with pytest.raises(LLMConnectTimeoutError):
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="x")]
        ):
            pass

    stream = ChunkStream([], gate=asyncio.Event())
    runtime = _runtime(
        lambda request: httpx.Response(200, stream=stream),
        first_token_timeout_seconds=0.01,
    )
    await runtime.startup()
    with pytest.raises(LLMFirstTokenTimeoutError):
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="x")]
        ):
            pass
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_response_and_releases_permit() -> None:
    stream = ChunkStream([], gate=asyncio.Event())
    runtime = _runtime(lambda request: httpx.Response(200, stream=stream), max_concurrency=1)
    await runtime.startup()
    adapter = runtime.create_adapter()

    async def consume() -> None:
        async for _ in adapter.generate_stream([ChatMessage(role="user", content="x")]):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert adapter.semaphore._value == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_total_timeout_after_first_output() -> None:
    gate = asyncio.Event()

    class FirstThenBlock(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield _sse({"choices": [{"delta": {"content": "Xin"}}]})
            await gate.wait()

        async def aclose(self) -> None:
            return None

    runtime = _runtime(
        lambda request: httpx.Response(200, stream=FirstThenBlock()),
        total_timeout_seconds=0.02,
        first_token_timeout_seconds=0.01,
        connect_timeout_seconds=0.01,
    )
    await runtime.startup()
    with pytest.raises(LLMTotalTimeoutError):
        async for _ in runtime.create_adapter().generate_stream(
            [ChatMessage(role="user", content="x")]
        ):
            pass


def test_circuit_breaker_single_half_open_probe() -> None:
    now = [0.0]
    breaker = CircuitBreaker(1, 5.0, time_func=lambda: now[0])
    permit = breaker.check_allow_request()
    breaker.record_failure(LLMProviderUnavailableError("down"), permit)
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(LLMCircuitOpenError):
        breaker.check_allow_request()
    now[0] = 5.0
    probe = breaker.check_allow_request()
    assert breaker.state is CircuitState.HALF_OPEN
    with pytest.raises(LLMCircuitOpenError):
        breaker.check_allow_request()
    breaker.record_success(probe)
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_closing_half_open_stream_releases_probe() -> None:
    now = [0.0]
    breaker = CircuitBreaker(1, 1.0, time_func=lambda: now[0])
    breaker.record_failure(
        LLMProviderUnavailableError("down"), breaker.check_allow_request()
    )
    now[0] = 1.0
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=ChunkStream([_sse({"choices": [{"delta": {"content": "x"}}]})]),
            )
        ),
        base_url="http://omniroute.test/v1",
    )
    adapter = OmniRouteLLMAdapter(
        OmniRouteLLMConfig(api_key="test"), client, breaker, asyncio.Semaphore(1)
    )
    stream = adapter.generate_stream([ChatMessage(role="user", content="x")])
    await anext(stream)
    await stream.aclose()
    probe = breaker.check_allow_request()
    breaker.record_success(probe)
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_half_open_admission_releases_probe() -> None:
    now = [0.0]
    breaker = CircuitBreaker(1, 1.0, time_func=lambda: now[0])
    breaker.record_failure(
        LLMProviderUnavailableError("down"), breaker.check_allow_request()
    )
    now[0] = 1.0
    semaphore = asyncio.Semaphore(0)
    client = httpx.AsyncClient(base_url="http://omniroute.test/v1")
    adapter = OmniRouteLLMAdapter(
        OmniRouteLLMConfig(api_key="test"), client, breaker, semaphore
    )

    async def consume() -> None:
        async for _ in adapter.generate_stream([ChatMessage(role="user", content="x")]):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    probe = breaker.check_allow_request()
    breaker.record_success(probe)
    await client.aclose()


def test_retry_after_and_readiness_fail_closed() -> None:
    assert parse_retry_after("-1") is None
    assert parse_retry_after("9999") == 600.0
    settings = Settings(environment="test", llm_provider="omniroute", llm_api_key="")
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["reason"] == "llm_runtime_not_ready"
