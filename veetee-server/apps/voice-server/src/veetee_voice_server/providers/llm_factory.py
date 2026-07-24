from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from veetee_voice_server.providers.cliproxy import CliProxyApiLlmProvider
from veetee_voice_server.providers.groq import GroqCloudLlmProvider
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider


class LlmProviderFactory(Protocol):
    def __call__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        reasoning_effort: str,
        config: Mapping[str, Any],
    ) -> NineRouterLlmProvider: ...


def _openai_compatible_provider(
    *,
    base_url: str,
    model: str,
    api_key: str,
    reasoning_effort: str,
    config: Mapping[str, Any],
) -> NineRouterLlmProvider:
    normalized_config = dict(config)
    completion_token_parameter = normalized_config.get(
        "completionTokenParameter", "max_tokens"
    )
    if completion_token_parameter not in {"max_tokens", "max_completion_tokens"}:
        completion_token_parameter = "max_tokens"
    return NineRouterLlmProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        config=normalized_config,
        completion_token_parameter=completion_token_parameter,
    )


_LLM_PROVIDER_FACTORIES: Mapping[str, LlmProviderFactory] = {
    "groq-cloud": GroqCloudLlmProvider,
    "openai-compatible-cliproxyapi": CliProxyApiLlmProvider,
    "cliproxyapi": CliProxyApiLlmProvider,
}


def create_llm_provider(
    *,
    adapter: str,
    base_url: str,
    model: str,
    api_key: str,
    reasoning_effort: str,
    config: Mapping[str, Any],
) -> NineRouterLlmProvider:
    factory = _LLM_PROVIDER_FACTORIES.get(
        adapter.strip().lower(),
        _openai_compatible_provider,
    )
    return factory(
        base_url=base_url,
        model=model,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        config=config,
    )
