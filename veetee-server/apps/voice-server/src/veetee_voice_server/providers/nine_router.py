from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.providers.contracts import (
    LlmEvent,
    LlmRequest,
    LlmStreamDone,
    LlmTextDelta,
    LlmToolCallFragment,
)


class NineRouterProviderError(RuntimeError):
    pass


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

    async def complete_json(
        self, *, system_prompt: str, user_prompt: str, context: OperationContext
    ) -> dict[str, Any]:
        """Run a short structured call for admission/planning without streaming prose."""
        context.checkpoint()
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self._model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "reasoning_effort": self._reasoning_effort,
            },
            timeout=context.remaining_seconds,
        )
        if response.is_error:
            detail = (await response.aread())[:500].decode(errors="replace")
            raise NineRouterProviderError(f"9router returned HTTP {response.status_code}: {detail}")
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) for item in content if isinstance(item, Mapping)
            )
        parsed = json.loads(str(content))
        if not isinstance(parsed, dict):
            raise NineRouterProviderError("9router structured response was not an object")
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
                    f"9router returned HTTP {response.status_code}: {detail}"
                )
            finish_reason: str | None = None
            async for data in self._sse_data(response):
                context.checkpoint()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as error:
                    raise NineRouterProviderError("Invalid JSON in 9router SSE event") from error
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
        if request.tool_result is not None:
            tool_json = json.dumps(request.tool_result, ensure_ascii=False)
            user_content = f"{user_content}\n\nTool result:\n{tool_json}"
        messages: list[dict[str, str]] = []
        system_prompt = getattr(request, "system_prompt", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
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
