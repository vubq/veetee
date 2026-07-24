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
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            reasoning_effort=reasoning_effort,
            config=config,
            provider_label="Groq Cloud",
            completion_token_parameter="max_completion_tokens",
            client=client,
        )
