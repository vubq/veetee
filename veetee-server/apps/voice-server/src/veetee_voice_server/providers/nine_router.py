from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal

import httpx
from jsonschema import Draft202012Validator

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.evidence import input_evidence_payload
from veetee_voice_server.providers.contracts import (
    LlmEvent,
    LlmRequest,
    LlmStreamDone,
    LlmTextDelta,
    LlmToolCallFragment,
)


class NineRouterProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        status_code: int | None = None,
        retryable: bool = False,
        finish_reason: str | None = None,
        output_characters: int = 0,
        schema_validator: str | None = None,
        schema_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.finish_reason = finish_reason
        self.output_characters = max(0, output_characters)
        self.schema_validator = schema_validator
        self.schema_path = schema_path


class NineRouterLlmProvider:
    """OpenAI-compatible Chat Completions adapter for the local 9Router process."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        reasoning_effort: str = "none",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._reasoning_effort = reasoning_effort
        self._client = client or httpx.AsyncClient(timeout=None)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self, context: OperationContext) -> bool:
        response = await self._client.get(
            f"{self._base_url}/models",
            headers=self._headers(),
            timeout=context.remaining_seconds,
        )
        return response.is_success

    async def prewarm(self, context: OperationContext) -> None:
        result = await self.complete_json(
            system_prompt='Return only a JSON object with {"ready":true}.',
            user_prompt="Warm the configured model for a latency-sensitive voice session.",
            context=context,
        )
        if result.get("ready") is not True:
            raise NineRouterProviderError("9router prewarm returned an invalid response")

    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        context: OperationContext,
        schema: Mapping[str, Any] | None = None,
        schema_name: str = "veetee_return_json",
        schema_transport: Literal["tool_call", "json_object", "json_schema"] = "tool_call",
        max_output_tokens: int | None = None,
        validate_schema: bool = True,
    ) -> dict[str, Any]:
        """Stream a structured call so planners do not wait for the full HTTP body."""
        context.checkpoint()
        payload = {
            "model": self._model,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning_effort": self._reasoning_effort,
            "temperature": 0,
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max(64, min(max_output_tokens, 4_096))
        if schema is None or schema_transport == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif schema_transport == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            }
        else:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": schema_name,
                        "description": "Return the requested structured object as arguments.",
                        "parameters": dict(schema),
                    },
                }
            ]
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": schema_name},
            }
        content_parts: list[str] = []
        argument_parts: list[str] = []
        collecting_arguments = False
        finish_reason: str | None = None
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=context.remaining_seconds,
        ) as response:
            if response.is_error:
                detail = (await response.aread())[:500].decode(errors="replace")
                raise NineRouterProviderError(
                    f"9router returned HTTP {response.status_code}: {detail}",
                    code="http_error",
                    status_code=response.status_code,
                    retryable=response.status_code in {408, 409, 425, 429}
                    or response.status_code >= 500,
                )
            async for data in self._sse_data(response):
                context.checkpoint()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as error:
                    raise NineRouterProviderError(
                        "Invalid JSON in 9router structured SSE event",
                        code="invalid_sse_json",
                    ) from error
                for parsed_event in self._events_from_payload(event):
                    if isinstance(parsed_event, LlmTextDelta):
                        content_parts.append(parsed_event.text)
                    elif isinstance(parsed_event, LlmToolCallFragment):
                        if parsed_event.name is not None:
                            collecting_arguments = parsed_event.name == schema_name
                        if collecting_arguments and parsed_event.arguments_fragment:
                            argument_parts.append(parsed_event.arguments_fragment)
                    elif isinstance(parsed_event, LlmStreamDone):
                        finish_reason = parsed_event.finish_reason
        raw_value = "".join(argument_parts or content_parts)
        if not raw_value.strip():
            raise NineRouterProviderError(
                "9router structured response was empty",
                code="empty_structured_output",
                finish_reason=finish_reason,
            )
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise NineRouterProviderError(
                "9router structured response was not valid JSON",
                code=(
                    "structured_output_truncated"
                    if finish_reason in {"length", "max_tokens"}
                    else "invalid_structured_json"
                ),
                finish_reason=finish_reason,
                output_characters=len(raw_value),
            ) from error
        if not isinstance(parsed, dict):
            raise NineRouterProviderError(
                "9router structured response was not an object",
                code="structured_not_object",
                finish_reason=finish_reason,
                output_characters=len(raw_value),
            )
        if schema is not None and validate_schema:
            validation_error = next(
                Draft202012Validator(dict(schema)).iter_errors(parsed), None
            )
            if validation_error is not None:
                raise NineRouterProviderError(
                    "9router structured response did not match the requested schema",
                    code="structured_schema_mismatch",
                    retryable=True,
                    finish_reason=finish_reason,
                    output_characters=len(raw_value),
                    schema_validator=str(validation_error.validator)[:64],
                    schema_path=".".join(
                        str(part)[:64] for part in tuple(validation_error.path)[:8]
                    ),
                )
        context.checkpoint()
        return parsed

    async def stream(
        self, request: LlmRequest, context: OperationContext
    ) -> AsyncIterator[LlmEvent]:
        context.checkpoint()
        payload = self._payload(request)
        headers = {**self._headers(), "Accept": "text/event-stream"}
        async with self._client.stream(
            "POST",
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=context.remaining_seconds,
        ) as response:
            if response.is_error:
                body = await response.aread()
                detail = body[:500].decode(errors="replace")
                raise NineRouterProviderError(
                    f"9router returned HTTP {response.status_code}: {detail}",
                    code="http_error",
                    status_code=response.status_code,
                    retryable=response.status_code in {408, 409, 425, 429}
                    or response.status_code >= 500,
                )
            finish_reason: str | None = None
            async for data in self._sse_data(response):
                context.checkpoint()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as error:
                    raise NineRouterProviderError(
                        "Invalid JSON in 9router SSE event",
                        code="invalid_sse_json",
                    ) from error
                for parsed in self._events_from_payload(event):
                    if isinstance(parsed, LlmStreamDone):
                        finish_reason = parsed.finish_reason
                    else:
                        yield parsed
            yield LlmStreamDone(finish_reason)

    async def _sse_data(self, response: httpx.Response) -> AsyncIterator[str]:
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if line == "":
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines.clear()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield "\n".join(data_lines)

    def _events_from_payload(self, payload: Mapping[str, Any]) -> list[LlmEvent]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        choice = choices[0]
        if not isinstance(choice, Mapping):
            return []
        output: list[LlmEvent] = []
        delta = choice.get("delta")
        if isinstance(delta, Mapping):
            content = delta.get("content")
            if isinstance(content, str) and content:
                output.append(LlmTextDelta(content))
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, Mapping) and item.get("type") == "text":
                        text = item.get("text")
                        if isinstance(text, str) and text:
                            output.append(LlmTextDelta(text))
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, Mapping):
                        continue
                    function = call.get("function")
                    if not isinstance(function, Mapping):
                        continue
                    arguments = function.get("arguments")
                    output.append(
                        LlmToolCallFragment(
                            call_id=call.get("id") if isinstance(call.get("id"), str) else None,
                            name=(
                                function.get("name")
                                if isinstance(function.get("name"), str)
                                else None
                            ),
                            arguments_fragment=arguments if isinstance(arguments, str) else "",
                        )
                    )
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            output.append(LlmStreamDone(finish_reason))
        # reasoning_content/reasoning fields are intentionally ignored and never reach TTS.
        return output

    def _payload(self, request: LlmRequest) -> dict[str, Any]:
        user_content = request.transcript.text
        admission = getattr(request, "admission", None)
        turn_metadata = {
            "locale": request.transcript.locale,
            "asr": {
                "confidence": request.transcript.confidence,
                "stability": request.transcript.stability,
            },
            "admission": (
                {
                    "decision": admission.disposition.value,
                    "confidence": admission.confidence,
                    "addressed_to_robot": admission.addressed_to_robot,
                    "reason_code": admission.reason_code,
                }
                if admission is not None
                else None
            ),
            "dialogue_act": request.plan.dialogue_act.value,
            "plan": {
                "action": request.plan.action.value,
                "intent": request.plan.intent,
                "response_required": request.plan.response_required,
            },
            "input_evidence": input_evidence_payload(request.transcript.input_evidence),
            "context_message_count": len(request.transcript.context),
        }
        user_content = (
            f"{user_content}\n\nTurn metadata (JSON): "
            f"{json.dumps(turn_metadata, ensure_ascii=False, separators=(',', ':'))}"
        )
        if request.tool_result is not None:
            tool_json = json.dumps(request.tool_result, ensure_ascii=False)
            user_content = f"{user_content}\n\nTool result:\n{tool_json}"
        messages: list[dict[str, str]] = []
        system_prompt = getattr(request, "system_prompt", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for item in request.transcript.context:
            messages.append({"role": item.role, "content": item.text})
        messages.append({"role": "user", "content": user_content})
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": True,
            "messages": messages,
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        if request.plan.intent:
            payload["metadata"] = {"veetee_intent": request.plan.intent}
        return payload

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}
