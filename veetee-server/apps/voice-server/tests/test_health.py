from __future__ import annotations

from time import monotonic

import pytest
from httpx import ASGITransport, AsyncClient

from veetee_voice_server.app import (
    _complete_conversation_gate_json,
    _LlmReadinessProbe,
    _planner_system_prompt,
    create_app,
)
from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.providers.tools import RegistryToolBroker
from veetee_voice_server.readiness import ComponentHealth

pytestmark = pytest.mark.asyncio


async def test_liveness_and_request_id() -> None:
    app = create_app(Settings(environment="test"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live", headers={"x-request-id": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "voice-server"}
    assert response.headers["x-request-id"] == "test-request"


async def test_readiness_fails_only_for_required_components() -> None:
    app = create_app(Settings(environment="test"))

    async def required_failure() -> ComponentHealth:
        return ComponentHealth("redis", healthy=False, required=True, detail="unreachable")

    async def optional_failure() -> ComponentHealth:
        return ComponentHealth("quality-asr", healthy=False, required=False)

    app.state.readiness.register(required_failure)
    app.state.readiness.register(optional_failure)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert len(response.json()["components"]) == 2


async def test_empty_registry_is_ready_during_phase_zero() -> None:
    app = create_app(Settings(environment="test"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "components": []}


async def test_default_websocket_route_uses_only_the_veetee_namespace() -> None:
    app = create_app(Settings(environment="test", _env_file=None))  # type: ignore[call-arg]
    paths = {route.path for route in app.routes}

    assert "/veetee/v1/" in paths
    assert "/xiaozhi/v1/" not in paths


async def test_manager_mcp_internal_routes_require_service_token() -> None:
    settings = Settings(
        environment="test",
        manager_internal_token="test-service-token",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unauthorized = await client.get("/internal/v1/devices/device-1/mcp/tools")
        offline = await client.get(
            "/internal/v1/devices/device-1/mcp/tools",
            headers={"authorization": "Bearer test-service-token"},
        )

    assert unauthorized.status_code == 401
    assert offline.status_code == 409
    assert offline.json()["detail"] == "Device has no active voice session"


async def test_planner_prompt_cannot_invent_tools_for_empty_registry() -> None:
    profile = SessionProfile.defaults(Settings(environment="test", require_device_auth=False))
    prompt = _planner_system_prompt(profile, RegistryToolBroker())
    assert "available tool catalog: []" in prompt
    assert "never invent a tool name" in prompt
    assert "ambiguous or missing details, admission must be accepted" in prompt
    assert "not hard-coded phrase rules" in prompt


async def test_conversation_gate_forces_the_full_structured_schema() -> None:
    class CapturingLlm:
        def __init__(self) -> None:
            self.arguments: dict[str, object] = {}

        async def complete_json(self, **arguments: object) -> dict[str, object]:
            self.arguments = arguments
            return {
                "admission": {
                    "decision": "accepted",
                    "confidence": 0.95,
                    "addressed_to_robot": 0.95,
                    "reason_code": "speech_relevant",
                },
                "dialogue_act": "follow_up",
                "plan": {
                    "action": "respond",
                    "locale": "vi-VN",
                    "intent": "conversation.follow_up",
                    "response_required": True,
                    "response_text": "Tôi vẫn đang theo dõi câu chuyện.",
                    "tool_call": None,
                },
            }

    llm = CapturingLlm()
    profile = SessionProfile.defaults(Settings(environment="test", require_device_auth=False))
    context = OperationContext(
        "session",
        "turn",
        1,
        CancellationToken(),
        monotonic() + 1.0,
    )

    result = await _complete_conversation_gate_json(  # type: ignore[arg-type]
        llm,
        profile,
        RegistryToolBroker(),
        {"transcript": "Gke vậy sao?", "conversation_context": []},
        context,
    )

    schema = llm.arguments["schema"]
    assert isinstance(schema, dict)
    assert schema["required"] == ["admission", "dialogue_act", "plan"]
    assert llm.arguments["schema_name"] == "veetee_conversation_gate"
    assert llm.arguments["schema_transport"] == "json_schema"
    assert llm.arguments["validate_schema"] is False
    assert llm.arguments["max_output_tokens"] == 512
    assert '"published_agent"' in str(llm.arguments["user_prompt"])
    assert "Published agent runtime context" in str(llm.arguments["system_prompt"])
    assert result["admission"]["decision"] == "accepted"  # type: ignore[index]


async def test_llm_readiness_retries_a_failed_startup_prewarm() -> None:
    class RecoveringLlm:
        def __init__(self) -> None:
            self.prewarm_calls = 0

        async def prewarm(self, _: object) -> None:
            self.prewarm_calls += 1
            if self.prewarm_calls == 1:
                raise RuntimeError("temporary failure")

        async def check_health(self, _: object) -> bool:
            return True

    provider = RecoveringLlm()
    probe = _LlmReadinessProbe(provider, 1.0, prewarmed=False)  # type: ignore[arg-type]

    first = await probe()
    second = await probe()

    assert first == ComponentHealth("llm", healthy=False, required=True, detail="prewarm_failed")
    assert second == ComponentHealth("llm", healthy=True, required=True)
    assert provider.prewarm_calls == 2
