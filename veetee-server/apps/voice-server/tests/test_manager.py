from __future__ import annotations

import httpx
import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.manager import DeviceContext, ManagerClient, SessionProfile


def test_session_profile_applies_config_with_runtime_safety_bounds() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    profile = SessionProfile.from_payload(
        {
            "agentId": "agent-1",
            "version": 7,
            "defaultLocale": "vi-VN",
            "persona": "Trợ lý gia đình ngắn gọn.",
            "conversation": {
                "firstInputSeconds": 0,
                "betweenTurnsSeconds": 45,
                "closingGraceSeconds": 999,
                "maxSessionSeconds": 99_999,
                "llmSeconds": 12,
            },
            "providers": [
                {
                    "kind": "llm",
                    "baseUrl": "http://127.0.0.1:20128/v1",
                    "model": "cx/configured-model",
                    "reasoningEffort": "low",
                }
            ],
        },
        settings,
    )

    assert profile.agent_id == "agent-1"
    assert profile.policy.first_input_seconds == 3.0
    assert profile.policy.between_turns_seconds == 45.0
    assert profile.policy.closing_grace_seconds == 60.0
    assert profile.policy.max_session_seconds == 3_600.0
    assert profile.llm_model == "cx/configured-model"
    assert profile.llm_reasoning_effort == "low"


def test_session_profile_uses_configurable_local_persona_fallback() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        default_persona="Configured local persona",
        max_session_seconds=720,
    )
    profile = SessionProfile.defaults(settings)
    assert profile.persona == "Configured local persona"
    assert profile.policy.max_session_seconds == 720


@pytest.mark.asyncio
async def test_manager_authenticates_device_and_caches_immutable_config() -> None:
    config_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal config_calls
        assert request.headers["authorization"] == "Bearer internal-test-token"
        if request.url.path.endswith("/devices/authenticate"):
            return httpx.Response(
                201,
                json={
                    "deviceId": "device-1",
                    "tenantId": "tenant-1",
                    "agentId": "agent-1",
                    "configVersion": 3,
                },
            )
        if request.url.path.endswith("/agent-configs/agent-1"):
            config_calls += 1
            return httpx.Response(
                200,
                json={
                    "agentId": "agent-1",
                    "version": 3,
                    "defaultLocale": "vi-VN",
                    "persona": "Veetee test",
                },
            )
        return httpx.Response(404)

    settings = Settings(
        environment="test",
        manager_api_url="http://manager.test",
        manager_internal_token="internal-test-token",
    )
    client = httpx.AsyncClient(
        base_url="http://manager.test", transport=httpx.MockTransport(handler)
    )
    manager = ManagerClient(settings, client=client)
    device = await manager.authenticate_device("esp32-1", "device-token")
    assert device == DeviceContext("device-1", "tenant-1", "agent-1", 3)
    first = await manager.session_profile(device)
    second = await manager.session_profile(device)
    await client.aclose()

    assert first is second
    assert first.persona == "Veetee test"
    assert config_calls == 1


@pytest.mark.asyncio
async def test_manager_resolves_ordered_provider_chain_without_exposing_it_to_device() -> None:
    resolve_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal resolve_calls
        if request.url.path.endswith("/agent-configs/agent-1"):
            return httpx.Response(
                200,
                json={
                    "agentId": "agent-1",
                    "version": 4,
                    "defaultLocale": "vi-VN",
                    "providerChains": [
                        {
                            "kind": "llm",
                            "locale": "vi-VN",
                            "providers": [
                                {"id": "11111111-1111-4111-8111-111111111111"},
                                {"id": "22222222-2222-4222-8222-222222222222"},
                            ],
                        }
                    ],
                },
            )
        if request.url.path.endswith("/providers/resolve"):
            resolve_calls += 1
            return httpx.Response(
                201,
                json=[
                    {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "kind": "llm",
                        "adapter": "openai-compatible-primary",
                        "model": "primary-model",
                        "baseUrl": "http://127.0.0.1:21001/v1",
                        "secret": "primary-secret",
                        "priority": 10,
                        "locales": ["vi-VN"],
                    },
                    {
                        "id": "22222222-2222-4222-8222-222222222222",
                        "kind": "llm",
                        "adapter": "openai-compatible-fallback",
                        "model": "fallback-model",
                        "baseUrl": "http://127.0.0.1:21002/v1",
                        "priority": 20,
                        "locales": ["vi-VN"],
                    },
                ],
            )
        return httpx.Response(404)

    settings = Settings(
        environment="test",
        manager_api_url="http://manager.test",
        manager_internal_token="internal-test-token",
        VEETEE_9ROUTER_API_KEY="settings-fallback-key",  # type: ignore[call-arg]
    )
    client = httpx.AsyncClient(
        base_url="http://manager.test", transport=httpx.MockTransport(handler)
    )
    manager = ManagerClient(settings, client=client)
    device = DeviceContext("device-1", "tenant-1", "agent-1", 4)

    profile = await manager.session_profile(device)
    cached = await manager.session_profile(device)
    await client.aclose()

    assert profile is cached
    assert [endpoint.model for endpoint in profile.llm_chain] == [
        "primary-model",
        "fallback-model",
    ]
    assert profile.llm_chain[0].api_key == "primary-secret"
    assert profile.llm_chain[1].api_key == "settings-fallback-key"
    assert resolve_calls == 1


@pytest.mark.asyncio
async def test_manager_publishes_redacted_conversation_event_batch() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["body"] = request.content.decode()
        return httpx.Response(201, json={"accepted": 1})

    settings = Settings(
        environment="test",
        manager_api_url="http://manager.test",
        manager_internal_token="internal-test-token",
    )
    client = httpx.AsyncClient(
        base_url="http://manager.test", transport=httpx.MockTransport(handler)
    )
    manager = ManagerClient(settings, client=client)
    accepted = await manager.publish_conversation_events(
        "4b6fbf00-4072-4ab5-b06e-a2884749d206",
        [
            {
                "eventId": "71b469fa-9fa1-4926-902f-004833a2473d",
                "sessionId": "session_12345678",
                "generation": 1,
                "eventType": "listen.start",
                "payload": {"source": "button"},
                "occurredAt": "2026-07-22T04:30:00.000Z",
            }
        ],
    )
    await client.aclose()

    assert accepted == 1
    assert captured["authorization"] == "Bearer internal-test-token"
    assert '"deviceId":"4b6fbf00-4072-4ab5-b06e-a2884749d206"' in str(captured["body"])
