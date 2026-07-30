from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import httpx

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider


class GroqCloudLlmProvider(NineRouterLlmProvider):
    """Groq Cloud Chat Completions adapter.

    Groq intentionally remains a normal chain candidate. It is only used when
    the published agent explicitly selects this provider (and can be a fallback
    only when the agent explicitly puts it later in the same chain).
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        reasoning_effort: str = "none",
        config: Mapping[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Supported Groq chat models expose OpenAI-compatible native function
        # calling. Keep responseFormat auto by default so the shared structured
        # planner transport uses its forced tool call; operators can still select
        # json_object explicitly for a model that lacks native tool support.
        normalized_config = dict(config or {})
        normalized_config.setdefault("responseFormat", "auto")
        supports_reasoning = self._supports_reasoning_model(model)
        if not supports_reasoning:
            normalized_config.pop("reasoningEffort", None)
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            reasoning_effort=reasoning_effort if supports_reasoning else "",
            config=normalized_config,
            provider_label="Groq Cloud",
            completion_token_parameter="max_completion_tokens",
            client=client,
        )
        self._supports_reasoning = supports_reasoning

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
        effective_transport = schema_transport
        if (
            self._config.get("responseFormat") == "auto"
            and schema_transport == "json_schema"
        ):
            effective_transport = "tool_call"
        return await super().complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context=context,
            schema=schema,
            schema_name=schema_name,
            schema_transport=effective_transport,
            max_output_tokens=max_output_tokens,
            validate_schema=validate_schema,
        )

    def _payload(self, request: Any) -> dict[str, Any]:
        # Groq rejects the OpenAI-compatible metadata extension used by 9router.
        payload = super()._payload(request)
        payload.pop("metadata", None)
        if not self._supports_reasoning:
            payload.pop("reasoning_effort", None)
        return payload

    @staticmethod
    def _supports_reasoning_model(model: str) -> bool:
        normalized = model.lower()
        return normalized.startswith("qwen/") or normalized.startswith("openai/gpt-oss-")
