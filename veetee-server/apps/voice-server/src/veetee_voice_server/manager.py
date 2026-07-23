from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import httpx

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.types import ConversationPolicy
from veetee_voice_server.prompting import PromptConfiguration


class ManagerAuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceContext:
    device_id: str
    tenant_id: str
    agent_id: str | None
    config_version: int
    locale: str | None = None
    time_zone: str | None = None
    time_zone_offset_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class LabSessionContext:
    session_id: str
    tenant_id: str
    user_id: str
    agent_id: str
    config_version: int
    input_mode: str
    mcp_mode: str
    device_id: str | None


@dataclass(frozen=True, slots=True)
class LlmEndpoint:
    provider_id: str
    adapter: str
    base_url: str
    model: str
    api_key: str
    reasoning_effort: str


@dataclass(frozen=True, slots=True)
class SessionProfile:
    agent_id: str | None
    config_version: int
    agent_name: str
    locale: str
    interaction_mode: str
    persona: str
    prompt: PromptConfiguration
    device_locale: str | None
    device_time_zone: str | None
    device_time_zone_offset_minutes: int | None
    goodbye_text: str
    policy: ConversationPolicy
    llm_chain: tuple[LlmEndpoint, ...]

    @property
    def llm_base_url(self) -> str:
        return self.llm_chain[0].base_url

    @property
    def llm_model(self) -> str:
        return self.llm_chain[0].model

    @property
    def llm_reasoning_effort(self) -> str:
        return self.llm_chain[0].reasoning_effort

    def render_system_prompt(self, tools: list[dict[str, Any]]) -> str:
        return self.prompt.render(
            agent_name=self.agent_name,
            locale=self.locale,
            persona=self.persona,
            interaction_mode=self.interaction_mode,
            config_version=self.config_version,
            tools=tools,
            device_locale=self.device_locale,
            device_time_zone=self.device_time_zone,
            device_time_zone_offset_minutes=self.device_time_zone_offset_minutes,
        )

    @classmethod
    def defaults(cls, settings: Settings) -> SessionProfile:
        return cls(
            agent_id=None,
            config_version=0,
            agent_name=settings.default_agent_name,
            locale=settings.default_locale,
            interaction_mode="auto",
            persona=settings.default_persona,
            prompt=PromptConfiguration.defaults(
                language=settings.default_prompt_language,
                time_zone=settings.default_prompt_timezone,
                personality=settings.default_personality,
            ),
            device_locale=None,
            device_time_zone=None,
            device_time_zone_offset_minutes=None,
            goodbye_text=settings.goodbye_text,
            policy=ConversationPolicy(
                first_input_seconds=settings.first_input_seconds,
                between_turns_seconds=settings.between_turns_seconds,
                closing_grace_seconds=settings.closing_grace_seconds,
                max_session_seconds=settings.max_session_seconds,
                total_turn_seconds=0.0,
                admission_seconds=1.0,
                planner_seconds=settings.planner_seconds,
                llm_seconds=20.0,
                tts_seconds=10.0,
                mcp_seconds=10.0,
            ),
            llm_chain=(
                LlmEndpoint(
                    provider_id="settings:9router",
                    adapter="openai-compatible-9router",
                    base_url=str(settings.nine_router_base_url),
                    model=settings.nine_router_model,
                    api_key=settings.nine_router_api_key,
                    reasoning_effort="none",
                ),
            ),
        )

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        settings: Settings,
        runtime_providers: list[dict[str, Any]] | None = None,
    ) -> SessionProfile:
        defaults = cls.defaults(settings)
        conversation = payload.get("conversation")
        if not isinstance(conversation, dict):
            conversation = {}
        locale = (
            _optional_string(payload.get("defaultLocale"))
            or _optional_string(payload.get("locale"))
            or defaults.locale
        )
        agent_name = _optional_string(payload.get("agentName")) or defaults.agent_name
        interaction_mode = (
            _optional_string(payload.get("interactionMode")) or defaults.interaction_mode
        )
        return cls(
            agent_id=_optional_string(payload.get("agentId")),
            config_version=_bounded_int(payload.get("version"), 0, 0, 2_147_483_647),
            agent_name=agent_name,
            locale=locale,
            interaction_mode=interaction_mode,
            persona=(
                _optional_string(payload.get("persona")) or ""
                if "persona" in payload
                else defaults.persona
            ),
            prompt=PromptConfiguration.from_payload(
                payload.get("prompt"),
                defaults=defaults.prompt,
            ),
            device_locale=_optional_string(payload.get("deviceLocale")),
            device_time_zone=_optional_string(payload.get("deviceTimeZone")),
            device_time_zone_offset_minutes=_optional_int(
                payload.get("deviceTimeZoneOffsetMinutes"), -840, 840
            ),
            goodbye_text=(
                _optional_string(conversation.get("timeoutGoodbye"))
                or _optional_string(payload.get("goodbyeText"))
                or defaults.goodbye_text
            ),
            policy=ConversationPolicy(
                first_input_seconds=_bounded_float(
                    conversation.get("firstInputSeconds"),
                    defaults.policy.first_input_seconds,
                    3.0,
                    300.0,
                ),
                between_turns_seconds=_bounded_float(
                    conversation.get("betweenTurnsSeconds"),
                    defaults.policy.between_turns_seconds,
                    3.0,
                    600.0,
                ),
                closing_grace_seconds=_bounded_float(
                    conversation.get("closingGraceSeconds"),
                    defaults.policy.closing_grace_seconds,
                    0.5,
                    60.0,
                ),
                max_session_seconds=_bounded_float(
                    conversation.get("maxSessionSeconds"),
                    defaults.policy.max_session_seconds,
                    0.0,
                    3_600.0,
                ),
                total_turn_seconds=_bounded_float(
                    conversation.get("totalTurnSeconds"),
                    defaults.policy.total_turn_seconds,
                    0.0,
                    60.0,
                ),
                admission_seconds=_bounded_float(
                    conversation.get("admissionSeconds"), 1.0, 0.1, 5.0
                ),
                planner_seconds=_bounded_float(
                    conversation.get("plannerSeconds"),
                    defaults.policy.planner_seconds,
                    0.5,
                    15.0,
                ),
                llm_seconds=_bounded_float(conversation.get("llmSeconds"), 20.0, 1.0, 45.0),
                tts_seconds=_bounded_float(conversation.get("ttsSeconds"), 10.0, 1.0, 30.0),
                mcp_seconds=_bounded_float(conversation.get("mcpSeconds"), 10.0, 0.5, 30.0),
                context_message_limit=_bounded_int(
                    conversation.get("contextMessageLimit"),
                    defaults.policy.context_message_limit,
                    2,
                    32,
                ),
                context_message_characters=_bounded_int(
                    conversation.get("contextMessageCharacters"),
                    defaults.policy.context_message_characters,
                    128,
                    4_000,
                ),
            ),
            llm_chain=_llm_chain(payload, runtime_providers or [], locale, defaults, settings),
        )


class ManagerClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=str(settings.manager_api_url).rstrip("/"),
            timeout=settings.manager_request_seconds,
        )
        self._owns_client = client is None
        self._profile_cache: dict[
            tuple[str, int, str | None, str | None, int | None], SessionProfile
        ] = {}
        self._cache_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get("/health/ready")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def authenticate_device(self, hardware_id: str, token: str) -> DeviceContext:
        response = await self._client.post(
            "/internal/v1/devices/authenticate",
            headers=self._service_headers(),
            json={"hardwareId": hardware_id, "token": token},
        )
        if response.status_code in {401, 403, 404}:
            raise ManagerAuthenticationError("Device identity was rejected")
        response.raise_for_status()
        payload = response.json()
        return DeviceContext(
            device_id=str(payload["deviceId"]),
            tenant_id=str(payload["tenantId"]),
            agent_id=_optional_string(payload.get("agentId")),
            config_version=_bounded_int(payload.get("configVersion"), 0, 0, 2_147_483_647),
            locale=_optional_string(payload.get("deviceLocale")),
            time_zone=_optional_string(payload.get("deviceTimeZone")),
            time_zone_offset_minutes=_optional_int(
                payload.get("deviceTimeZoneOffsetMinutes"), -840, 840
            ),
        )

    async def session_profile(self, device: DeviceContext) -> SessionProfile:
        if not device.agent_id or device.config_version <= 0:
            return SessionProfile.defaults(self._settings)
        cache_key = (
            device.agent_id,
            device.config_version,
            device.locale,
            device.time_zone,
            device.time_zone_offset_minutes,
        )
        cached = self._profile_cache.get(cache_key)
        if cached is not None:
            return cached
        async with self._cache_lock:
            cached = self._profile_cache.get(cache_key)
            if cached is not None:
                return cached
            response = await self._client.get(
                f"/internal/v1/agent-configs/{device.agent_id}",
                params={"version": device.config_version},
                headers=self._service_headers(),
            )
            response.raise_for_status()
            payload = response.json()
            provider_ids = _provider_ids(payload)
            runtime_providers: list[dict[str, Any]] = []
            if provider_ids:
                providers_response = await self._client.post(
                    "/internal/v1/providers/resolve",
                    headers=self._service_headers(),
                    json={"providerIds": provider_ids},
                )
                providers_response.raise_for_status()
                resolved = providers_response.json()
                if not isinstance(resolved, list):
                    raise ValueError("Manager provider resolver returned an invalid payload")
                runtime_providers = [item for item in resolved if isinstance(item, dict)]
            profile = SessionProfile.from_payload(
                payload,
                self._settings,
                runtime_providers=runtime_providers,
            )
            profile = replace(
                profile,
                agent_id=device.agent_id,
                config_version=device.config_version,
                device_locale=device.locale or profile.device_locale,
                device_time_zone=device.time_zone or profile.device_time_zone,
                device_time_zone_offset_minutes=(
                    device.time_zone_offset_minutes
                    if device.time_zone_offset_minutes is not None
                    else profile.device_time_zone_offset_minutes
                ),
            )
            self._profile_cache[cache_key] = profile
            return profile

    async def consume_lab_session(self, token: str) -> LabSessionContext:
        response = await self._client.post(
            "/internal/v1/lab/sessions/consume",
            headers=self._service_headers(),
            json={"token": token},
        )
        if response.status_code in {401, 403, 404}:
            raise ManagerAuthenticationError("Lab session was rejected")
        response.raise_for_status()
        payload = response.json()
        input_mode = _optional_string(payload.get("inputMode"))
        mcp_mode = _optional_string(payload.get("mcpMode"))
        if input_mode not in {"text", "audio_replay", "live_mic"}:
            raise ValueError("Manager returned an invalid Lab input mode")
        if mcp_mode not in {"simulated", "selected_device", "disabled"}:
            raise ValueError("Manager returned an invalid Lab MCP mode")
        config_version = _bounded_int(payload.get("configVersion"), -1, 1, 2_147_483_647)
        if config_version <= 0:
            raise ValueError("Manager returned an invalid Lab config version")
        return LabSessionContext(
            session_id=str(payload["sessionId"]),
            tenant_id=str(payload["tenantId"]),
            user_id=str(payload["userId"]),
            agent_id=str(payload["agentId"]),
            config_version=config_version,
            input_mode=input_mode,
            mcp_mode=mcp_mode,
            device_id=_optional_string(payload.get("deviceId")),
        )

    async def publish_conversation_events(
        self, device_id: str, events: list[dict[str, Any]]
    ) -> int:
        response = await self._client.post(
            "/internal/v1/conversation-events/batch",
            headers=self._service_headers(),
            json={"deviceId": device_id, "events": events},
        )
        response.raise_for_status()
        payload = response.json()
        return _bounded_int(payload.get("accepted"), 0, 0, len(events))

    def _service_headers(self) -> dict[str, str]:
        token = self._settings.manager_internal_token
        if not token:
            raise ManagerAuthenticationError("Manager internal token is not configured")
        return {"Authorization": f"Bearer {token}"}


def _find_llm(payload: dict[str, Any]) -> dict[str, Any]:
    providers = payload.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict) and provider.get("kind") == "llm":
                return provider
    llm = payload.get("llm")
    return llm if isinstance(llm, dict) else {}


def _provider_ids(payload: dict[str, Any]) -> list[str]:
    chains = payload.get("providerChains")
    if not isinstance(chains, list):
        return []
    output: list[str] = []
    for chain in chains:
        if not isinstance(chain, dict):
            continue
        providers = chain.get("providers")
        if not isinstance(providers, list):
            continue
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_id = _optional_string(provider.get("id"))
            if provider_id and provider_id not in output:
                output.append(provider_id)
    return output


def _llm_chain(
    payload: dict[str, Any],
    runtime_providers: list[dict[str, Any]],
    locale: str,
    defaults: SessionProfile,
    settings: Settings,
) -> tuple[LlmEndpoint, ...]:
    runtime_by_id = {
        provider_id: provider
        for provider in runtime_providers
        if (provider_id := _optional_string(provider.get("id")))
    }
    chains = payload.get("providerChains")
    if isinstance(chains, list):
        for selected_locale in (locale, "*"):
            for chain in chains:
                if (
                    not isinstance(chain, dict)
                    or chain.get("kind") != "llm"
                    or chain.get("locale") != selected_locale
                ):
                    continue
                providers = chain.get("providers")
                if not isinstance(providers, list):
                    continue
                endpoints = tuple(
                    endpoint
                    for provider in providers
                    if isinstance(provider, dict)
                    and (endpoint := _llm_endpoint(provider, runtime_by_id, defaults, settings))
                    is not None
                )
                if endpoints:
                    return endpoints

    legacy = _find_llm(payload)
    endpoint = _llm_endpoint(legacy, runtime_by_id, defaults, settings)
    return (endpoint,) if endpoint is not None else defaults.llm_chain


def _llm_endpoint(
    published: dict[str, Any],
    runtime_by_id: dict[str, dict[str, Any]],
    defaults: SessionProfile,
    settings: Settings,
) -> LlmEndpoint | None:
    provider_id = _optional_string(published.get("id"))
    runtime = runtime_by_id.get(provider_id or "", published)
    if runtime.get("kind") not in {None, "llm"}:
        return None
    model = _optional_string(runtime.get("model")) or _optional_string(published.get("model"))
    base_url = _optional_string(runtime.get("baseUrl")) or _optional_string(
        published.get("baseUrl")
    )
    if not model or not base_url:
        return None
    return LlmEndpoint(
        provider_id=provider_id or f"legacy:{model}",
        adapter=(
            _optional_string(runtime.get("adapter"))
            or _optional_string(published.get("adapter"))
            or "openai-compatible"
        ),
        base_url=base_url,
        model=model,
        api_key=_optional_string(runtime.get("secret")) or settings.nine_router_api_key,
        # Voice turns favor predictable latency; published profiles cannot enable
        # hidden model reasoning unless Veetee introduces an explicit policy later.
        reasoning_effort="none",
    )


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: object, minimum: int, maximum: int) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return min(max(value, minimum), maximum)


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return default
    return min(max(float(value), minimum), maximum)


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, minimum), maximum)
