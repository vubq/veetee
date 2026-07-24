from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import httpx

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider


def _supports_strict_json_schema(value: object) -> bool:
    if isinstance(value, list):
        return all(_supports_strict_json_schema(item) for item in value)
    if not isinstance(value, Mapping):
        return True

    schema_type = value.get("type")
    includes_object = schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )
    if includes_object:
        properties = value.get("properties")
        required = value.get("required")
        if (
            value.get("additionalProperties") is not False
            or not isinstance(properties, Mapping)
            or not isinstance(required, list)
            or set(required) != set(properties)
        ):
            return False
    return all(_supports_strict_json_schema(item) for item in value.values())


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
        if normalized_config.get("responseFormat") == "json_schema":
            # Select strict schema per request after checking Codex constraints.
            normalized_config["responseFormat"] = "auto"
        configured_reasoning = normalized_config.get("reasoningEffort", reasoning_effort)
        if configured_reasoning == "none":
            # CLIProxyAPI maps "none" to a zero thinking budget, which Codex rejects
            # for strict structured responses. Omitting the field selects its default.
            normalized_config.pop("reasoningEffort", None)
            reasoning_effort = ""
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
            effective_transport == "json_schema"
            and schema is not None
            and not _supports_strict_json_schema(schema)
        ):
            # Codex strict schemas require closed objects with every property required.
            # JSON Object Mode preserves dynamic MCP arguments; local validation remains.
            effective_transport = "json_object"
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
