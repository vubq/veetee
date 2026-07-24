from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from veetee_voice_server.providers.nine_router import NineRouterLlmProvider


class CliProxyApiLlmProvider(NineRouterLlmProvider):
    """OpenAI-compatible adapter for the local CLIProxyAPI gateway."""

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
        normalized_config = dict(config or {})
        completion_token_parameter = normalized_config.get(
            "completionTokenParameter", "max_tokens"
        )
        if completion_token_parameter not in {"max_tokens", "max_completion_tokens"}:
            completion_token_parameter = "max_tokens"
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key,
            reasoning_effort=reasoning_effort,
            config=normalized_config,
            provider_label="CLIPROXYAPI",
            completion_token_parameter=completion_token_parameter,
            client=client,
        )
