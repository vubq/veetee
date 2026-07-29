from __future__ import annotations

import httpx
import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.memory import MemoryPolicy, MemoryScope
from veetee_voice_server.manager import (
    DeviceContext,
    LabSessionContext,
    ManagerClient,
    SessionProfile,
)


def test_session_profile_applies_config_with_runtime_safety_bounds() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    profile = SessionProfile.from_payload(
        {
            "agentId": "agent-1",
            "version": 7,
            "agentName": "Mây",
            "defaultLocale": "vi-VN",
            "persona": "Trợ lý gia đình ngắn gọn.",
            "prompt": {
                "template": (
                    "{{agent_name}} trả lời bằng {{language}}. "
                    "Vai trò: {{persona}}. Tính cách: {{personality}}."
                ),
                "language": "Tiếng Việt tự nhiên",
                "timeZone": "Asia/Bangkok",
                "personalityPresetId": "stubborn-reasoned",
                "personality": "Ngang bướng có lý.",
                "responseStyle": "Ngắn gọn.",
                "userAddress": "bạn",
            },
            "conversation": {
                "firstInputSeconds": 0,
                "betweenTurnsSeconds": 45,
                "closingGraceSeconds": 999,
                "maxSessionSeconds": 0,
                "totalTurnSeconds": 0,
                "llmFirstTokenSeconds": 4,
                "llmStreamIdleSeconds": 12,
                "ttsFirstAudioSeconds": 6,
                "ttsStreamIdleSeconds": 9,
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
    assert profile.agent_name == "Mây"
    assert profile.prompt.language == "Tiếng Việt tự nhiên"
    assert profile.prompt.personality_preset_id == "stubborn-reasoned"
    assert profile.policy.first_input_seconds == 3.0
    assert profile.policy.between_turns_seconds == 45.0
    assert profile.policy.closing_grace_seconds == 60.0
    assert profile.policy.max_session_seconds == 0.0
    assert profile.policy.total_turn_seconds == 0.0
    assert profile.policy.llm_first_token_seconds == 4.0
    assert profile.policy.llm_stream_idle_seconds == 12.0
    assert profile.policy.llm_total_seconds == 0.0
    assert profile.policy.tts_first_audio_seconds == 6.0
    assert profile.policy.tts_stream_idle_seconds == 9.0
    assert profile.policy.tts_total_seconds == 0.0
    assert profile.llm_model == "cx/configured-model"
    assert profile.llm_reasoning_effort == "none"


def test_session_profile_defaults_to_incremental_prose_streaming() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        VEETEE_CLIPROXY_API_KEY="cliproxy-key",  # type: ignore[call-arg]
    )

    profile = SessionProfile.defaults(settings)

    assert profile.llm_chain[0].provider_id == "settings:cliproxyapi"
    assert profile.llm_chain[0].adapter == "openai-compatible-cliproxyapi"
    assert profile.llm_chain[0].base_url == "http://127.0.0.1:8317/v1"
    assert profile.llm_chain[0].model == "gpt-5.6-terra"
    assert profile.llm_chain[0].api_key == "cliproxy-key"
    assert profile.llm_chain[0].config["streamProseResponse"] is True


def test_session_profile_memory_is_default_off_and_bounded_from_snapshot() -> None:
    settings = Settings(environment="test", require_device_auth=False)

    defaults = SessionProfile.defaults(settings)
    profile = SessionProfile.from_payload(
        {
            "memoryPolicy": {
                "enabled": True,
                "consent": True,
                "storeMessages": True,
                "storeFacts": True,
                "retentionDays": 999,
                "maxMessages": 999,
                "maxMessageCharacters": 99999,
                "maxContextCharacters": 99999,
                "factRetentionDays": 999,
                "maxFacts": 999,
                "maxFactCharacters": 99999,
            }
        },
        settings,
    )

    assert defaults.memory_policy == MemoryPolicy()
    assert defaults.memory_policy.active is False
    assert profile.memory_policy.active is True
    assert profile.memory_policy.retention_days == 30
    assert profile.memory_policy.max_messages == 40
    assert profile.memory_policy.max_message_characters == 4_000
    assert profile.memory_policy.max_context_characters == 12_000
    assert profile.memory_policy.fact_retention_days == 365
    assert profile.memory_policy.max_facts == 100
    assert profile.memory_policy.max_fact_characters == 2_000


def test_session_profile_enables_incremental_prose_for_legacy_provider_config() -> None:
    settings = Settings(environment="test", require_device_auth=False)

    profile = SessionProfile.from_payload(
        {
            "providers": [
                {
                    "kind": "llm",
                    "baseUrl": "http://127.0.0.1:20128/v1",
                    "model": "cx/configured-model",
                    "config": {},
                }
            ]
        },
        settings,
    )

    assert profile.llm_chain[0].config["streamProseResponse"] is True


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

def test_legacy_v1_stream_seconds_map_to_renewable_idle_watchdogs() -> None:
    settings = Settings(environment="test", require_device_auth=False)

    profile = SessionProfile.from_payload(
        {"conversation": {"llmSeconds": 12, "ttsSeconds": 7}},
        settings,
    )

    assert profile.policy.llm_stream_idle_seconds == 12.0
    assert profile.policy.tts_stream_idle_seconds == 7.0
    assert profile.policy.llm_total_seconds == 0.0
    assert profile.policy.tts_total_seconds == 0.0


def test_explicit_stream_idle_fields_override_legacy_aliases() -> None:
    settings = Settings(environment="test", require_device_auth=False)

    profile = SessionProfile.from_payload(
        {
            "conversation": {
                "llmSeconds": 12,
                "llmStreamIdleSeconds": 8,
                "ttsSeconds": 7,
                "ttsStreamIdleSeconds": 6,
            }
        },
        settings,
    )

    assert profile.policy.llm_stream_idle_seconds == 8.0
    assert profile.policy.tts_stream_idle_seconds == 6.0
    assert profile.policy.llm_total_seconds == 0.0
    assert profile.policy.tts_total_seconds == 0.0


def test_session_profile_treats_zero_parent_turn_ceiling_as_unlimited() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    profile = SessionProfile.from_payload(
        {
            "conversation": {
                "maxSessionSeconds": 0,
                "totalTurnSeconds": 0,
            }
        },
        settings,
    )
    assert profile.policy.max_session_seconds == 0
    assert profile.policy.total_turn_seconds == 0


def test_session_profile_preserves_an_explicitly_empty_agent_persona() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        default_persona="Configured local persona",
    )
    profile = SessionProfile.from_payload(
        {
            "agentName": "VeeTee",
            "defaultLocale": "vi-VN",
            "persona": "",
            "prompt": {
                "template": "{{agent_name}} speaks {{language}}.",
                "language": "Tiếng Việt",
                "timeZone": "",
                "personalityPresetId": "",
                "personality": "",
            },
        },
        settings,
    )

    assert profile.persona == ""
    assert profile.prompt.personality == ""


def test_session_profile_resolves_selected_tts_voice_and_provider_config() -> None:
    settings = Settings(environment="test", require_device_auth=False)
    profile = SessionProfile.from_payload(
        {
            "version": 9,
            "defaultLocale": "vi-VN",
            "providerChains": [
                {
                    "kind": "tts",
                    "locale": "vi-VN",
                    "providers": [
                        {
                            "id": "vieneu-provider",
                            "kind": "tts",
                            "adapter": "vieneu-local",
                            "model": "vieneu-tts-v3-turbo",
                            "config": {
                                "voice": "Trúc Ly",
                                "style": "tu_nhien",
                                "supportsPitch": False,
                                "outputSampleRate": 24000,
                            },
                        }
                    ],
                }
            ],
            "voice": {
                "providerId": "vieneu-provider",
                "voiceId": "Trúc Ly",
                "gender": "female",
                "style": "doc_truyen",
                "rate": 1.1,
                "pitchHz": 0,
                "volume": 1,
            },
        },
        settings,
        runtime_providers=[
            {
                "id": "vieneu-provider",
                "kind": "tts",
                "adapter": "vieneu-local",
                "model": "vieneu-tts-v3-turbo",
                "config": {
                    "voice": "Trúc Ly",
                    "style": "tu_nhien",
                    "supportsPitch": False,
                    "outputSampleRate": 24000,
                },
            }
        ],
    )
    assert profile.voice is not None
    assert profile.voice.voice_id == "Trúc Ly"
    assert profile.voice.style == "doc_truyen"
    assert profile.tts_endpoint is not None
    assert profile.tts_endpoint.adapter == "vieneu-local"
    assert profile.tts_endpoint.config["outputSampleRate"] == 24000


def test_session_profile_uses_cliproxy_secret_without_reusing_9router_key() -> None:
    settings = Settings(
        environment="test",
        require_device_auth=False,
        VEETEE_9ROUTER_API_KEY="router-key",  # type: ignore[call-arg]
        VEETEE_CLIPROXY_API_KEY="cliproxy-key",  # type: ignore[call-arg]
    )
    profile = SessionProfile.from_payload(
        {
            "providerChains": [
                {
                    "kind": "llm",
                    "locale": "vi-VN",
                    "providers": [
                        {
                            "id": "cliproxy-provider",
                            "kind": "llm",
                            "adapter": "openai-compatible-cliproxyapi",
                            "model": "gpt-5.6-terra",
                            "baseUrl": "http://127.0.0.1:8317/v1",
                        }
                    ],
                }
            ]
        },
        settings,
    )

    assert profile.llm_chain[0].api_key == "cliproxy-key"
    assert profile.llm_chain[0].api_key != "router-key"


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
                    "deviceLocale": "en-US",
                    "deviceTimeZone": "America/New_York",
                    "deviceTimeZoneOffsetMinutes": -240,
                },
            )
        if request.url.path.endswith("/agent-configs/agent-1"):
            config_calls += 1
            return httpx.Response(
                200,
                json={
                    "agentId": "agent-1",
                    "version": 3,
                    "agentName": "Mây",
                    "defaultLocale": "vi-VN",
                    "persona": "Veetee test",
                    "prompt": {
                        "template": (
                            "SNAPSHOT {{agent_name}}/{{language}}/{{persona}}/"
                            "{{personality}}/{{config_version}}"
                        ),
                        "language": "Tiếng Việt kiểm thử",
                        "timeZone": "Asia/Bangkok",
                        "personalityPresetId": "stubborn-reasoned",
                        "personality": "Prompt đã publish",
                    },
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
    assert device == DeviceContext(
        "device-1",
        "tenant-1",
        "agent-1",
        3,
        "en-US",
        "America/New_York",
        -240,
    )
    first = await manager.session_profile(device)
    second = await manager.session_profile(
        DeviceContext(
            "lab:session-1",
            "tenant-1",
            "agent-1",
            3,
            "en-US",
            "America/New_York",
            -240,
        )
    )
    await client.aclose()

    assert first is second
    assert first.persona == "Veetee test"
    assert first.device_locale == "en-US"
    assert first.device_time_zone == "America/New_York"
    rendered = first.render_system_prompt([])
    assert "SNAPSHOT Mây/Tiếng Việt kiểm thử/Veetee test/Prompt đã publish/3" == rendered
    assert second.render_system_prompt([]) == rendered
    assert config_calls == 1


@pytest.mark.asyncio
async def test_manager_resolves_chain_without_cross_gateway_secret_fallback() -> None:
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
    assert profile.llm_chain[1].api_key == ""
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


@pytest.mark.asyncio
async def test_manager_memory_contract_uses_scoped_internal_routes() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer internal-test-token"
        if request.url.path.endswith("/memory/context"):
            assert request.url.params["agentId"] == "agent-1"
            assert request.url.params["deviceId"] == "device-1"
            assert request.url.params["configVersion"] == "7"
            return httpx.Response(200, json={"policy": {}, "messages": [], "memoryFacts": []})
        if request.url.path.endswith("/memory/messages/batch"):
            return httpx.Response(201, json={"accepted": 2, "duplicates": 0})
        if request.url.path.endswith("/memory/facts/batch"):
            return httpx.Response(
                201, json={"accepted": 1, "duplicates": 0, "rejected": 0}
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
    scope = MemoryScope("device-1", "agent-1", 7, MemoryPolicy())

    context = await manager.load_memory_context(scope)
    message_result = await manager.append_memory_messages(
        scope,
        [
            {"idempotencyKey": "u", "role": "user"},
            {"idempotencyKey": "a", "role": "assistant"},
        ],
    )
    fact_result = await manager.append_memory_facts(
        scope,
        [{"idempotencyKey": "f", "category": "preference"}],
    )
    await client.aclose()

    assert context["messages"] == []
    assert message_result == (2, 0)
    assert fact_result == (1, 0, 0)
    assert [request.url.path for request in requests] == [
        "/internal/v1/memory/context",
        "/internal/v1/memory/messages/batch",
        "/internal/v1/memory/facts/batch",
    ]


@pytest.mark.asyncio
async def test_manager_consumes_one_use_lab_context_through_internal_api() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer internal-test-token"
        assert request.url.path == "/internal/v1/lab/sessions/consume"
        assert request.content == b'{"token":"signed-lab-token"}'
        return httpx.Response(
            201,
            json={
                "sessionId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "tenantId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "userId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "agentId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "configVersion": 4,
                "inputMode": "text",
                "mcpMode": "simulated",
            },
        )

    settings = Settings(
        environment="test",
        manager_api_url="http://manager.test",
        manager_internal_token="internal-test-token",
    )
    client = httpx.AsyncClient(
        base_url="http://manager.test", transport=httpx.MockTransport(handler)
    )
    manager = ManagerClient(settings, client=client)

    context = await manager.consume_lab_session("signed-lab-token")
    await client.aclose()

    assert context == LabSessionContext(
        session_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        tenant_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        user_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        agent_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        config_version=4,
        input_mode="text",
        mcp_mode="simulated",
        device_id=None,
    )
