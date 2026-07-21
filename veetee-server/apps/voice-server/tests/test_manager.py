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
    assert profile.llm_model == "cx/configured-model"
    assert profile.llm_reasoning_effort == "low"


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
