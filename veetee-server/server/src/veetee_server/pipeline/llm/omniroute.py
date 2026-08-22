"""OmniRoute Groq streaming adapter with bounded SSE parsing and lifecycle controls."""

from __future__ import annotations

import asyncio
import codecs
import email.utils
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from uuid import uuid4

import httpx

from .contract import (
    ChatMessage,
    CompletedToolCall,
    LLMCompletedEvent,
    LLMStartedEvent,
    LLMStreamEvent,
    LLMTextDeltaEvent,
    LLMToolCallDeltaEvent,
    LLMUsage,
    LLMUsageEvent,
    OmniRouteLLMConfig,
)
from .errors import (
    LLMAdmissionTimeoutError,
    LLMCircuitOpenError,
    LLMConnectTimeoutError,
    LLMEmptyResponseError,
    LLMFirstTokenTimeoutError,
    LLMMalformedStreamError,
    LLMNotReadyError,
    LLMOversizedStreamError,
    LLMProviderAuthError,
    LLMProviderError,
    LLMProviderRateLimitError,
    LLMProviderUnavailableError,
    LLMTotalTimeoutError,
)

logger = logging.getLogger("veetee.llm")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    epoch: int
    probe: bool = False


class CircuitBreaker:
    """Monotonic circuit breaker allowing exactly one half-open probe."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 10.0,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._time = time_func
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._epoch = 0

    @property
    def state(self) -> CircuitState:
        if (
            self._state is CircuitState.OPEN
            and self._time() - self._opened_at >= self.cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN
        return self._state

    def check_allow_request(self) -> CircuitPermit:
        state = self.state
        if state is CircuitState.OPEN or (
            state is CircuitState.HALF_OPEN and self._probe_in_flight
        ):
            raise LLMCircuitOpenError("OmniRoute circuit breaker is open")
        if state is CircuitState.HALF_OPEN:
            self._probe_in_flight = True
            return CircuitPermit(self._epoch, probe=True)
        return CircuitPermit(self._epoch)

    def record_success(self, permit: CircuitPermit) -> None:
        if permit.epoch != self._epoch or self._state is CircuitState.OPEN:
            return
        if self._state is CircuitState.HALF_OPEN and not permit.probe:
            return
        self._failures = 0
        self._probe_in_flight = False
        self._state = CircuitState.CLOSED

    def record_cancelled(self, permit: CircuitPermit | None) -> None:
        if permit is not None and permit.epoch == self._epoch and permit.probe:
            self._probe_in_flight = False

    def record_failure(self, exc: Exception, permit: CircuitPermit) -> None:
        if permit.epoch != self._epoch:
            return
        transient = isinstance(
            exc,
            (
                LLMProviderUnavailableError,
                LLMProviderRateLimitError,
                LLMConnectTimeoutError,
                LLMFirstTokenTimeoutError,
                LLMTotalTimeoutError,
            ),
        )
        if not transient:
            if permit.probe:
                self._probe_in_flight = False
            return
        was_probe = self._state is CircuitState.HALF_OPEN
        self._failures += 1
        self._probe_in_flight = False
        if was_probe or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self._time()
            self._epoch += 1


class SSEDecoder:
    """Incremental UTF-8 SSE decoder dispatching complete data events."""

    def __init__(self, max_response_bytes: int) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._data_lines: list[str] = []
        self._total = 0
        self._limit = max_response_bytes
        self._at_start = True

    def feed_bytes(self, chunk: bytes) -> list[str]:
        self._total += len(chunk)
        if self._total > self._limit:
            raise LLMOversizedStreamError("LLM response exceeded configured byte limit")
        try:
            decoded = self._decoder.decode(chunk)
            if self._at_start and decoded:
                decoded = decoded.removeprefix("\ufeff")
                self._at_start = False
            self._buffer += decoded
        except UnicodeDecodeError as exc:
            raise LLMMalformedStreamError("LLM stream is not valid UTF-8") from exc
        return self._drain_lines(final=False)

    def finish(self) -> list[str]:
        try:
            self._buffer += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise LLMMalformedStreamError("LLM stream ended inside a UTF-8 sequence") from exc
        # SSE dispatches only on a blank line. Pending data at EOF is truncated.
        return self._drain_lines(final=True)

    def _drain_lines(self, *, final: bool) -> list[str]:
        events: list[str] = []
        while True:
            positions = [
                position
                for position in (self._buffer.find("\n"), self._buffer.find("\r"))
                if position >= 0
            ]
            if not positions:
                break
            position = min(positions)
            if self._buffer[position] == "\r" and position + 1 == len(self._buffer) and not final:
                break
            line = self._buffer[:position]
            consumed = 2 if self._buffer[position : position + 2] == "\r\n" else 1
            self._buffer = self._buffer[position + consumed :]
            event = self._consume_line(line)
            if event is not None:
                events.append(event)
        if final and self._buffer:
            event = self._consume_line(self._buffer.removesuffix("\r"))
            self._buffer = ""
            if event is not None:
                events.append(event)
        return events

    def _consume_line(self, line: str) -> str | None:
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data_lines.append(value)
            if sum(len(item.encode("utf-8")) for item in self._data_lines) > self._limit:
                raise LLMOversizedStreamError("LLM SSE event exceeded configured byte limit")
        return None

    def _dispatch(self) -> str | None:
        if not self._data_lines:
            return None
        data = "\n".join(self._data_lines)
        self._data_lines.clear()
        return data


def parse_retry_after(value: str | None, now: float | None = None) -> float | None:
    """Parse Retry-After seconds or HTTP date and cap it at ten minutes."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            seconds = parsed.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0:
        return None
    return min(seconds, 600.0)


class OmniRouteLLMRuntime:
    """Application-scoped owner of the OmniRoute HTTP client and breaker."""

    def __init__(
        self,
        config: OmniRouteLLMConfig,
        http_client: httpx.AsyncClient | None = None,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._client = http_client
        self._external_client = http_client is not None
        self._started = False
        self._closed = False
        self._breaker = CircuitBreaker(
            config.circuit_breaker_failure_threshold,
            config.circuit_breaker_cooldown_seconds,
            time_func,
        )
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

    async def startup(self) -> None:
        if self._closed:
            raise LLMNotReadyError("OmniRoute runtime has been shut down")
        if self._started:
            return
        if not self.config.api_key and not self._external_client:
            raise LLMNotReadyError("OmniRoute API key is not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.config.base_url.rstrip("/"))
        self._started = True

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        if self._client is not None and not self._external_client:
            await self._client.aclose()
        self._client = None

    @property
    def is_ready(self) -> bool:
        return self._started and not self._closed and self._client is not None

    def create_adapter(self, model_override: str | None = None) -> OmniRouteLLMAdapter:
        """Creates a turn-scoped adapter over the app-scoped shared resources.

        ``model_override`` swaps only the request model on a copy of the
        frozen config (per adapter/turn); the HTTP client, circuit breaker and
        concurrency semaphore stay application-scoped and are never recreated.
        An empty or ``None`` override keeps the runtime default model.
        """
        if not self.is_ready or self._client is None:
            raise LLMNotReadyError("OmniRoute runtime is not ready")
        config = (
            replace(self.config, model=model_override)
            if model_override and model_override.strip()
            else self.config
        )
        return OmniRouteLLMAdapter(
            config,
            self._client,
            self._breaker,
            self._semaphore,
        )


class OmniRouteLLMAdapter:
    """OpenAI-compatible streaming provider targeting OmniRoute."""

    def __init__(
        self,
        config: OmniRouteLLMConfig,
        client: httpx.AsyncClient,
        circuit_breaker: CircuitBreaker,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self.config = config
        self._client = client
        self.circuit_breaker = circuit_breaker
        self.semaphore = semaphore

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.total_timeout_seconds
        permit: CircuitPermit | None = None
        try:
            admission = min(
                self.config.admission_timeout_seconds,
                self.config.total_timeout_seconds,
            )
            await asyncio.wait_for(self.semaphore.acquire(), admission)
        except asyncio.CancelledError:
            self.circuit_breaker.record_cancelled(permit)
            raise
        except TimeoutError as exc:
            self.circuit_breaker.record_cancelled(permit)
            if loop.time() >= deadline:
                raise LLMTotalTimeoutError("LLM total timeout exceeded") from exc
            raise LLMAdmissionTimeoutError("LLM admission timeout exceeded") from exc

        try:
            if loop.time() >= deadline:
                raise LLMTotalTimeoutError("LLM total timeout exceeded")
            permit = self.circuit_breaker.check_allow_request()
            async for event in self._request(
                messages, tools, temperature, max_tokens, deadline
            ):
                yield event
            self.circuit_breaker.record_success(permit)
        except (asyncio.CancelledError, GeneratorExit):
            self.circuit_breaker.record_cancelled(permit)
            raise
        except Exception as exc:
            if permit is not None:
                self.circuit_breaker.record_failure(exc, permit)
            raise
        finally:
            self.semaphore.release()

    async def _request(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        deadline: float,
    ) -> AsyncGenerator[LLMStreamEvent]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [self._message_payload(message) for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "reasoning_effort": self.config.reasoning_effort,
        }
        if tools is not None:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        request_id = f"omniroute-{uuid4().hex[:8]}"
        yield LLMStartedEvent(
            provider="omniroute",
            model=self.config.model,
            request_id=request_id,
            timestamp_ms=time.time() * 1000.0,
        )
        request = self._client.build_request(
            "POST", "/chat/completions", json=payload, headers=headers
        )
        loop = asyncio.get_running_loop()
        connect_budget = min(
            self.config.connect_timeout_seconds,
            deadline - loop.time(),
        )
        if connect_budget <= 0:
            raise LLMTotalTimeoutError("LLM total timeout exceeded")
        try:
            response = await asyncio.wait_for(
                self._client.send(request, stream=True), connect_budget
            )
        except TimeoutError as exc:
            if loop.time() >= deadline:
                raise LLMTotalTimeoutError("LLM total timeout exceeded") from exc
            raise LLMConnectTimeoutError("Could not connect to OmniRoute") from exc
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise LLMConnectTimeoutError("Could not connect to OmniRoute") from exc
        except (
            httpx.PoolTimeout,
            httpx.WriteTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
        ) as exc:
            raise LLMProviderUnavailableError("OmniRoute transport timed out") from exc
        except httpx.TransportError as exc:
            raise LLMProviderUnavailableError("OmniRoute transport failed") from exc

        try:
            self._raise_for_status(response)
            decoder = SSEDecoder(self.config.max_response_bytes)
            text_parts: list[str] = []
            tool_parts: dict[int, dict[str, str]] = {}
            usage: LLMUsage | None = None
            finish_reason: str | None = None
            first_output = False
            first_output_deadline = loop.time() + self.config.first_token_timeout_seconds
            done = False
            saw_done = False
            iterator = response.aiter_bytes().__aiter__()
            while not done:
                now = loop.time()
                budget = deadline - now
                if not first_output:
                    budget = min(budget, first_output_deadline - now)
                if budget <= 0:
                    if first_output or now >= deadline:
                        raise LLMTotalTimeoutError("LLM total timeout exceeded")
                    raise LLMFirstTokenTimeoutError("LLM first output timeout exceeded")
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), budget)
                except StopAsyncIteration:
                    events = decoder.finish()
                    done = True
                except TimeoutError as exc:
                    if first_output or loop.time() >= deadline:
                        raise LLMTotalTimeoutError("LLM total timeout exceeded") from exc
                    raise LLMFirstTokenTimeoutError(
                        "LLM first output timeout exceeded"
                    ) from exc
                except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as exc:
                    raise LLMProviderUnavailableError("OmniRoute stream was interrupted") from exc
                else:
                    events = decoder.feed_bytes(chunk)
                for data in events:
                    if data == "[DONE]":
                        done = True
                        saw_done = True
                        break
                    parsed = self._parse_chunk(data)
                    parsed_events, output_seen, new_usage, new_finish = self._consume_chunk(
                        parsed, text_parts, tool_parts
                    )
                    first_output = first_output or output_seen
                    usage = new_usage or usage
                    finish_reason = new_finish or finish_reason
                    for event in parsed_events:
                        yield event

            if not saw_done:
                raise LLMMalformedStreamError("OmniRoute stream ended before [DONE]")
            tool_calls = self._complete_tool_calls(tool_parts)
            text = "".join(text_parts)
            if not text and not tool_calls:
                raise LLMEmptyResponseError("LLM returned no final text or tool call")
            yield LLMCompletedEvent(
                text=text,
                tool_calls=tool_calls,
                finish_reason=finish_reason or "stop",
                usage=usage,
            )
        finally:
            await response.aclose()

    @staticmethod
    def _message_payload(message: ChatMessage) -> dict[str, Any]:
        result: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.name is not None:
            result["name"] = message.name
        if message.tool_calls is not None:
            result["tool_calls"] = message.tool_calls
        if message.tool_call_id is not None:
            result["tool_call_id"] = message.tool_call_id
        return result

    @staticmethod
    def _parse_chunk(data: str) -> dict[str, Any]:
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMMalformedStreamError("Malformed JSON in OmniRoute stream") from exc
        if not isinstance(parsed, dict):
            raise LLMMalformedStreamError("OmniRoute stream chunk must be an object")
        return parsed

    @staticmethod
    def _consume_chunk(
        chunk: dict[str, Any],
        text_parts: list[str],
        tool_parts: dict[int, dict[str, str]],
    ) -> tuple[list[LLMStreamEvent], bool, LLMUsage | None, str | None]:
        events: list[LLMStreamEvent] = []
        output_seen = False
        usage: LLMUsage | None = None
        raw_usage = chunk.get("usage")
        if raw_usage is not None and not isinstance(raw_usage, dict):
            raise LLMMalformedStreamError("LLM usage must be an object")
        if isinstance(raw_usage, dict):
            try:
                details = raw_usage.get("completion_tokens_details")
                reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
                values = [
                    raw_usage.get("prompt_tokens", 0),
                    raw_usage.get("completion_tokens", 0),
                    raw_usage.get("total_tokens", 0),
                ]
                if any(type(value) is not int or value < 0 for value in values):
                    raise ValueError
                if reasoning is not None and (type(reasoning) is not int or reasoning < 0):
                    raise ValueError
                usage = LLMUsage(
                    prompt_tokens=values[0],
                    completion_tokens=values[1],
                    total_tokens=values[2],
                    reasoning_tokens=reasoning,
                )
            except (TypeError, ValueError) as exc:
                raise LLMMalformedStreamError("LLM usage contains invalid token counts") from exc
            events.append(LLMUsageEvent(usage))
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events, output_seen, usage, None
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMMalformedStreamError("LLM choice must be an object")
        finish = choice.get("finish_reason")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return events, output_seen, usage, str(finish) if finish else None
        content = delta.get("content")
        if isinstance(content, str) and content:
            text_parts.append(content)
            events.append(LLMTextDeltaEvent(content))
            output_seen = True
        raw_tools = delta.get("tool_calls")
        if raw_tools is not None and not isinstance(raw_tools, list):
            raise LLMMalformedStreamError("Tool calls must be a list")
        if isinstance(raw_tools, list):
            for raw_tool in raw_tools:
                index = raw_tool.get("index") if isinstance(raw_tool, dict) else None
                if type(index) is not int or index < 0:
                    raise LLMMalformedStreamError("Tool delta requires an integer index")
                target = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if raw_tool.get("id") is not None:
                    if not isinstance(raw_tool["id"], str):
                        raise LLMMalformedStreamError("Tool id must be a string")
                    target["id"] += raw_tool["id"]
                function = raw_tool.get("function")
                name: str | None = None
                arguments = ""
                if isinstance(function, dict):
                    if function.get("name") is not None:
                        if not isinstance(function["name"], str):
                            raise LLMMalformedStreamError("Tool name must be a string")
                        name = function["name"]
                        target["name"] += name
                    if function.get("arguments") is not None:
                        if not isinstance(function["arguments"], str):
                            raise LLMMalformedStreamError("Tool arguments must be a string")
                        arguments = function["arguments"]
                        target["arguments"] += arguments
                events.append(
                    LLMToolCallDeltaEvent(index, raw_tool.get("id"), name, arguments)
                )
                output_seen = True
        return events, output_seen, usage, str(finish) if finish else None

    @staticmethod
    def _complete_tool_calls(parts: dict[int, dict[str, str]]) -> list[CompletedToolCall]:
        completed: list[CompletedToolCall] = []
        for index in sorted(parts):
            item = parts[index]
            if not item["id"] or not item["name"]:
                raise LLMMalformedStreamError("Completed tool call is missing id or name")
            try:
                parsed = json.loads(item["arguments"])
            except json.JSONDecodeError as exc:
                raise LLMMalformedStreamError("Tool arguments are not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise LLMMalformedStreamError("Tool arguments must be a JSON object")
            completed.append(
                CompletedToolCall(
                    item["id"], item["name"], item["arguments"], parsed
                )
            )
        return completed

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise LLMProviderAuthError("OmniRoute authentication failed", status)
        if status == 429:
            raise LLMProviderRateLimitError(
                "OmniRoute rate limit exceeded",
                parse_retry_after(response.headers.get("retry-after")),
            )
        if status >= 500:
            raise LLMProviderUnavailableError("OmniRoute is unavailable", status)
        if status != 200:
            raise LLMProviderError("OmniRoute rejected the request", status)
