from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import httpx

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.types import ConversationPolicy


class ManagerAuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceContext:
    device_id: str
    tenant_id: str
    agent_id: str | None
    config_version: int


@dataclass(frozen=True, slots=True)
class SessionProfile:
    agent_id: str | None
    config_version: int
    locale: str
    persona: str
    goodbye_text: str
    policy: ConversationPolicy
    llm_base_url: str
    llm_model: str
    llm_reasoning_effort: str

    @classmethod
    def defaults(cls, settings: Settings) -> SessionProfile:
        return cls(
            agent_id=None,
            config_version=0,
            locale=settings.default_locale,
            persona=settings.default_persona,
            goodbye_text=settings.goodbye_text,
            policy=ConversationPolicy(
                first_input_seconds=settings.first_input_seconds,
                between_turns_seconds=settings.between_turns_seconds,
                closing_grace_seconds=settings.closing_grace_seconds,
                max_session_seconds=settings.max_session_seconds,
                total_turn_seconds=30.0,
                admission_seconds=1.0,
                planner_seconds=settings.planner_seconds,
                llm_seconds=20.0,
                tts_seconds=10.0,
                mcp_seconds=10.0,
            ),
            llm_base_url=str(settings.nine_router_base_url),
            llm_model=settings.nine_router_model,
            llm_reasoning_effort=settings.nine_router_reasoning_effort,
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any], settings: Settings) -> SessionProfile:
        defaults = cls.defaults(settings)
        conversation = payload.get("conversation")
        if not isinstance(conversation, dict):
            conversation = {}
        llm = _find_llm(payload)
        return cls(
            agent_id=_optional_string(payload.get("agentId")),
            config_version=_bounded_int(payload.get("version"), 0, 0, 2_147_483_647),
            locale=(
                _optional_string(payload.get("defaultLocale"))
                or _optional_string(payload.get("locale"))
                or defaults.locale
            ),
            persona=_optional_string(payload.get("persona")) or defaults.persona,
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
                    10.0,
                    3_600.0,
                ),
                total_turn_seconds=_bounded_float(
                    conversation.get("totalTurnSeconds"), 30.0, 5.0, 60.0
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
            ),
            llm_base_url=_optional_string(llm.get("baseUrl")) or defaults.llm_base_url,
            llm_model=_optional_string(llm.get("model")) or defaults.llm_model,
            llm_reasoning_effort=(
                _reasoning_effort(llm.get("reasoningEffort")) or defaults.llm_reasoning_effort
            ),
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
        self._profile_cache: dict[tuple[str, int], SessionProfile] = {}
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
        )

    async def session_profile(self, device: DeviceContext) -> SessionProfile:
        if not device.agent_id or device.config_version <= 0:
            return SessionProfile.defaults(self._settings)
        cache_key = (device.agent_id, device.config_version)
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
            profile = SessionProfile.from_payload(response.json(), self._settings)
            profile = replace(
                profile,
                agent_id=device.agent_id,
                config_version=device.config_version,
            )
            self._profile_cache[cache_key] = profile
            return profile

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


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _bounded_float(value: object, default: float, minimum: float, maximum: float) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return default
    return min(max(float(value), minimum), maximum)


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, minimum), maximum)


def _reasoning_effort(value: object) -> str | None:
    return value if value in {"none", "low", "medium", "high"} else None
