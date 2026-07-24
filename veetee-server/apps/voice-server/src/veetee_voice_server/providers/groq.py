from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

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
        # Groq JSON schema responses are not streamable, while JSON Object Mode
        # is supported by the general-purpose Llama model used by the seed.
        normalized_config = dict(config or {})
        normalized_config["responseFormat"] = "json_object"
        normalized_config.pop("reasoningEffort", None)
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            reasoning_effort="",
            config=normalized_config,
            provider_label="Groq Cloud",
            completion_token_parameter="max_completion_tokens",
            client=client,
        )

    def _payload(self, request: Any) -> dict[str, Any]:
        # Groq rejects the OpenAI-compatible metadata extension used by 9router.
        payload = super()._payload(request)
        payload.pop("metadata", None)
        payload.pop("reasoning_effort", None)
        return payload
