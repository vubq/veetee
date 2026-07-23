from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, status
from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field

from veetee_voice_server.config import Settings, get_settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    OperationDeadlineExceededError,
)
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.logging import configure_logging
from veetee_voice_server.manager import (
    DeviceContext,
    ManagerAuthenticationError,
    ManagerClient,
    SessionProfile,
)
from veetee_voice_server.providers.contracts import ToolBroker
from veetee_voice_server.providers.failover import (
    FailoverLlmProvider,
    LlmProviderCandidate,
    ProviderChainUnavailableError,
)
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
from veetee_voice_server.providers.nine_router import (
    NineRouterLlmProvider,
    NineRouterProviderError,
)
from veetee_voice_server.providers.semantic import StructuredConversationGate
from veetee_voice_server.providers.silero_vad import SileroVadModel
from veetee_voice_server.readiness import ComponentHealth, ReadinessRegistry
from veetee_voice_server.telemetry import ConversationTelemetryBuffer
from veetee_voice_server.transport.lab import (
    EmptyLabToolBroker,
    LabSession,
    SelectedDeviceLabToolBroker,
    SimulatedLabToolBroker,
)
from veetee_voice_server.transport.mcp import DeviceMcpError
from veetee_voice_server.transport.session import VoiceSession
from veetee_voice_server.transport.session_registry import (
    DeviceSessionRegistry,
    DeviceSessionUnavailableError,
)
from veetee_voice_server.transport.sink import ConversationSink

logger = structlog.get_logger(__name__)


def _published_agent_context(profile: SessionProfile) -> dict[str, object]:
    """Expose only bounded, non-secret agent/device config to the model."""

    return {
        "agent_id": profile.agent_id,
        "config_version": profile.config_version,
        "agent_name": profile.agent_name,
        "locale": profile.locale,
        "interaction_mode": profile.interaction_mode,
        "device_locale": profile.device_locale,
        "device_time_zone": profile.device_time_zone,
        "device_time_zone_offset_minutes": profile.device_time_zone_offset_minutes,
        "conversation_policy": {
            "first_input_seconds": profile.policy.first_input_seconds,
            "between_turns_seconds": profile.policy.between_turns_seconds,
            "closing_grace_seconds": profile.policy.closing_grace_seconds,
            "max_session_seconds": profile.policy.max_session_seconds,
            "total_turn_seconds": profile.policy.total_turn_seconds,
            "context_message_limit": profile.policy.context_message_limit,
        },
    }


class HealthResponse(BaseModel):
    status: str
    service: str


class ReadyResponse(BaseModel):
    status: str
    components: list[dict[str, object]]


class ManagerMcpCallRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    timeout_seconds: float = Field(default=10.0, ge=0.5, le=30.0)


def _planner_system_prompt(profile: SessionProfile, tools: ToolBroker) -> str:
    tool_catalog = tools.list_tools()
    catalog = json.dumps(tool_catalog, ensure_ascii=False, separators=(",", ":"))
    prompt_tool_names = [
        {"name": item["name"], "description": ""}
        for item in tool_catalog
        if isinstance(item.get("name"), str)
    ]
    agent_context = json.dumps(
        _published_agent_context(profile), ensure_ascii=False, separators=(",", ":")
    )
    return (
        "Return exactly one JSON object with admission, dialogue_act and plan. "
        "admission.decision: accepted|non_actionable|not_addressed|unclear|interrupt|end. "
        "admission must also include numeric confidence and addressed_to_robot in [0,1], "
        "plus reason_code from speech_relevant|non_speech|low_quality|not_addressed|"
        "self_echo|duplicate|low_confidence|semantic_interrupt|conversation_end|unclear|"
        "invalid_model_output. plan.action: respond|call_tool_then_respond|"
        "ask_clarification|execute_pending_tool|cancel_pending_tool|end_session|noop. "
        "dialogue_act: question|command|follow_up|answer|confirmation|denial|correction|"
        "clarification_answer|social|interrupt|end. plan must include action, locale, intent, "
        "response_required, response_text and tool_call; nullable fields must be explicit null. "
        "Use transcript, recent context, ASR and input_evidence together. A short reaction, "
        "slang, joke, correction, confirmation or follow-up is accepted when it is a natural "
        "part of this conversation; it need not be a standalone command or question. If an "
        "assistant-directed turn is ambiguous or missing details, admission must be accepted "
        "and action ask_clarification. unclear is only for genuinely conflicting admission "
        "evidence. non_actionable is only unusable linguistic signal, self-echo or duplicate; "
        "not_addressed is clear incidental speech. Named noise/media sources are benchmark "
        "categories, not hard-coded phrase rules. null evidence means unavailable, never zero. "
        "An accepted intentional turn must respond, clarify, use a valid tool or end; do not "
        "silently noop it. For a complete short answer, put directly speakable text in "
        "response_text. For an answer needing more detail, set response_text null so the "
        "runtime can stream the full natural response. Tool actions require response_text null. "
        "Only use an exact tool name from this available tool catalog: "
        f"{catalog}. When the catalog is empty, never invent a tool name."
        f"\n\nPublished agent runtime context (JSON): {agent_context}"
        f"\n\nPublished agent prompt:\n{profile.render_system_prompt(prompt_tool_names)}"
        "\n\nRuntime boundaries override conflicting published text: keep admission "
        "general and context-aware; never invent tool names/results; never expose secrets, "
        "internal scores or hidden reasoning; and pass every side effect through the "
        "deterministic tool policy."
    ).strip()


def _response_system_prompt(profile: SessionProfile, tools: ToolBroker) -> str:
    agent_context = json.dumps(
        _published_agent_context(profile), ensure_ascii=False, separators=(",", ":")
    )
    return (
        "Generate the assistant's natural spoken response for the current turn. "
        "Follow the published agent prompt, locale, personality and conversation context. "
        "Use the admission, ASR and plan metadata as context only; do not expose internal "
        "scores, planner rules, tool schemas or chain-of-thought. Never claim a tool action "
        "succeeded unless the supplied tool result says so. Keep the response directly "
        "speakable and appropriate for the current dialogue. "
        f"\n\nPublished agent runtime context (JSON): {agent_context}"
        f"\n\nPublished agent prompt:\n{profile.render_system_prompt(tools.list_tools())}"
        "\n\nRuntime boundaries override conflicting published text: never expose internal "
        "scores, hidden reasoning or secrets, and never claim an unconfirmed tool result."
    ).strip()


def _planner_output_schema(tools: ToolBroker) -> dict[str, object]:
    tool_names = [item["name"] for item in tools.list_tools() if isinstance(item.get("name"), str)]
    plan_properties: dict[str, object] = {
        "action": {
            "type": "string",
            "enum": [
                "respond",
                "call_tool_then_respond",
                "ask_clarification",
                "execute_pending_tool",
                "cancel_pending_tool",
                "end_session",
                "noop",
            ],
        },
        "locale": {"type": "string"},
        "intent": {"type": "string"},
        "response_required": {"type": "boolean"},
        "response_text": {"type": ["string", "null"], "maxLength": 600},
        "tool_call": {"type": "null"},
    }
    if tool_names:
        plan_properties["tool_call"] = {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "arguments"],
                    "properties": {
                        "name": {"type": "string", "enum": tool_names},
                        "arguments": {"type": "object"},
                    },
                },
                {"type": "null"},
            ]
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["admission", "dialogue_act", "plan"],
        "properties": {
            "admission": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "decision",
                    "confidence",
                    "addressed_to_robot",
                    "reason_code",
                ],
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": [
                            "accepted",
                            "non_actionable",
                            "not_addressed",
                            "unclear",
                            "interrupt",
                            "end",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "addressed_to_robot": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": [
                            "speech_relevant",
                            "non_speech",
                            "low_quality",
                            "not_addressed",
                            "self_echo",
                            "duplicate",
                            "low_confidence",
                            "semantic_interrupt",
                            "conversation_end",
                            "unclear",
                            "invalid_model_output",
                        ],
                    },
                },
            },
            "dialogue_act": {
                "type": "string",
                "enum": [
                    "question",
                    "command",
                    "follow_up",
                    "answer",
                    "confirmation",
                    "denial",
                    "correction",
                    "clarification_answer",
                    "social",
                    "interrupt",
                    "end",
                ],
            },
            "plan": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action",
                    "locale",
                    "intent",
                    "response_required",
                    "response_text",
                    "tool_call",
                ],
                "properties": plan_properties,
            },
        },
    }


def _validated_planner_output(
    value: dict[str, Any], schema: dict[str, object], locale: str
) -> dict[str, Any]:
    normalized = dict(value)
    admission = value.get("admission")
    if isinstance(admission, dict):
        normalized_admission = dict(admission)
        addressed_to_robot = normalized_admission.get("addressed_to_robot")
        if isinstance(addressed_to_robot, bool):
            normalized_admission["addressed_to_robot"] = float(addressed_to_robot)
        normalized["admission"] = normalized_admission
    else:
        normalized_admission = None

    plan = value.get("plan")
    if isinstance(plan, dict) and normalized_admission is not None:
        normalized_plan = dict(plan)
        decision = normalized_admission.get("decision")
        dialogue_act = normalized.get("dialogue_act")
        tool_call = normalized_plan.get("tool_call")
        if "action" not in normalized_plan:
            if decision == "end" or dialogue_act == "end":
                normalized_plan["action"] = "end_session"
            elif decision in {
                "non_actionable",
                "not_addressed",
                "unclear",
                "interrupt",
            }:
                normalized_plan["action"] = "noop"
            elif isinstance(tool_call, dict):
                normalized_plan["action"] = "call_tool_then_respond"
            elif decision == "accepted":
                normalized_plan["action"] = "respond"
        action = normalized_plan.get("action")
        normalized_plan.setdefault("locale", locale)
        normalized_plan.setdefault("intent", "")
        normalized_plan.setdefault(
            "response_required",
            action
            not in {
                "noop",
                "cancel_pending_tool",
            },
        )
        normalized_plan.setdefault("response_text", None)
        normalized_plan.setdefault("tool_call", None)
        normalized["plan"] = normalized_plan

        if "dialogue_act" not in normalized:
            normalized["dialogue_act"] = (
                "end"
                if decision == "end"
                else "interrupt"
                if decision == "interrupt"
                else "answer"
            )
    validation_error = next(Draft202012Validator(schema).iter_errors(normalized), None)
    if validation_error is None:
        return normalized
    logger.warning(
        "conversation_gate_schema_rejected",
        validator=validation_error.validator,
        path=".".join(str(part) for part in validation_error.path),
    )
    return {
        "admission": {
            "decision": "unclear",
            "confidence": 0.0,
            "addressed_to_robot": 0.0,
            "reason_code": "invalid_model_output",
        },
        "dialogue_act": "clarification_answer",
        "plan": {
            "action": "noop",
            "locale": locale,
            "intent": "input.invalid_model_output",
            "response_required": False,
            "response_text": None,
            "tool_call": None,
        },
    }


def _degraded_conversation_gate(locale: str) -> dict[str, Any]:
    """Keep a linguistic turn conversational while disabling all tool execution."""

    return {
        "admission": {
            "decision": "accepted",
            "confidence": 0.5,
            "addressed_to_robot": 0.5,
            "reason_code": "invalid_model_output",
        },
        "dialogue_act": "answer",
        "plan": {
            "action": "respond",
            "locale": locale,
            "intent": "",
            "response_required": True,
            "response_text": None,
            "tool_call": None,
        },
    }


async def _complete_conversation_gate_json(
    llm: FailoverLlmProvider,
    profile: SessionProfile,
    tools: ToolBroker,
    payload: dict[str, object],
    context: OperationContext,
) -> dict[str, Any]:
    schema = _planner_output_schema(tools)
    system_prompt = _planner_system_prompt(profile, tools)
    payload = {**payload, "published_agent": _published_agent_context(profile)}
    user_prompt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    conversation_context = payload.get("conversation_context")
    context_message_count = (
        len(conversation_context) if isinstance(conversation_context, list) else 0
    )
    started_at = monotonic()
    logger.info(
        "conversation_gate_request",
        turn_id=context.turn_id,
        context_messages=context_message_count,
        system_prompt_chars=len(system_prompt),
        user_prompt_chars=len(user_prompt),
        schema_chars=len(json.dumps(schema, separators=(",", ":"))),
        tool_count=len(tools.list_tools()),
    )
    try:
        value = await llm.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context=context,
            schema=schema,
            schema_name="veetee_conversation_gate",
            schema_transport="json_schema",
            max_output_tokens=512,
            validate_schema=False,
        )
    except (NineRouterProviderError, ProviderChainUnavailableError, httpx.HTTPError) as error:
        logger.warning(
            "conversation_gate_provider_failed",
            turn_id=context.turn_id,
            error=type(error).__name__,
            provider_code=getattr(error, "code", None),
            status_code=getattr(error, "status_code", None),
            retryable=getattr(error, "retryable", None),
            finish_reason=getattr(error, "finish_reason", None),
            output_characters=getattr(error, "output_characters", None),
            schema_validator=getattr(error, "schema_validator", None),
            schema_path=getattr(error, "schema_path", None),
            fallback="respond_without_tools",
        )
        return _degraded_conversation_gate(profile.locale)
    logger.info(
        "conversation_gate_response",
        turn_id=context.turn_id,
        duration_ms=round((monotonic() - started_at) * 1_000, 1),
    )
    return _validated_planner_output(value, schema, profile.locale)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    readiness = ReadinessRegistry()
    runtime: dict[str, object] = {}
    llm_registry: dict[tuple[str, str, str, str, str], LlmProviderCandidate] = {}
    llm_chain_registry: dict[tuple[tuple[str, str, str, str, str], ...], FailoverLlmProvider] = {}
    device_sessions = DeviceSessionRegistry()
    lab_capacity_lock = asyncio.Lock()
    active_lab_sessions = 0

    def llm_for_profile(profile: SessionProfile) -> FailoverLlmProvider:
        keys: list[tuple[str, str, str, str, str]] = []
        candidates: list[LlmProviderCandidate] = []
        for endpoint in profile.llm_chain:
            secret_fingerprint = hashlib.sha256(endpoint.api_key.encode()).hexdigest()[:16]
            key = (
                endpoint.provider_id,
                endpoint.base_url,
                endpoint.model,
                endpoint.reasoning_effort,
                secret_fingerprint,
            )
            candidate = llm_registry.get(key)
            if candidate is None:
                candidate = LlmProviderCandidate(
                    endpoint.provider_id,
                    NineRouterLlmProvider(
                        base_url=endpoint.base_url,
                        model=endpoint.model,
                        api_key=endpoint.api_key,
                        reasoning_effort=endpoint.reasoning_effort,
                    ),
                )
                llm_registry[key] = candidate
            keys.append(key)
            candidates.append(candidate)
        chain_key = tuple(keys)
        chain = llm_chain_registry.get(chain_key)
        if chain is None:
            chain = FailoverLlmProvider(tuple(candidates))
            llm_chain_registry[chain_key] = chain
        return chain

    def engine_factory(
        arbiter: TurnArbiter,
        sink: ConversationSink,
        profile: SessionProfile,
        tool_broker: ToolBroker,
    ) -> ConversationEngine:
        asr_llm = llm_for_profile(profile)
        asr_tts = runtime.get("tts")
        if not isinstance(asr_tts, VieNeuTtsProvider):
            raise RuntimeError("voice runtime is not ready")

        async def gate_json(
            payload: dict[str, object], context: OperationContext
        ) -> dict[str, Any]:
            return await _complete_conversation_gate_json(
                asr_llm,
                profile,
                tool_broker,
                payload,
                context,
            )

        gate = StructuredConversationGate(gate_json, locale=profile.locale)
        system_prompt = _response_system_prompt(profile, tool_broker)
        return ConversationEngine(
            arbiter=arbiter,
            admission=gate,
            planner=gate,
            llm=asr_llm,
            tts=asr_tts,
            tools=tool_broker,
            sink=sink,
            policy=profile.policy,
            system_prompt=system_prompt,
            error_text=profile.conversation_error_text,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.environment != "test":
            asr = SherpaZipformerAsrProvider(
                resolved_settings.models_root / "sherpa-onnx-zipformer-vi-30m-int8",
                num_threads=resolved_settings.asr_threads,
            )
            vad_model = SileroVadModel(
                resolved_settings.models_root / "silero-vad/silero_vad.onnx",
                num_threads=resolved_settings.vad_threads,
            )
            tts = VieNeuTtsProvider(
                resolved_settings.models_root / "vieneu-v3-turbo",
                voice=resolved_settings.tts_voice,
                speed=resolved_settings.tts_speed,
                output_sample_rate=resolved_settings.tts_output_sample_rate,
                num_threads=resolved_settings.tts_threads,
                apply_watermark=resolved_settings.tts_apply_watermark,
            )
            default_llm = llm_for_profile(SessionProfile.defaults(resolved_settings))
            runtime.update(asr=asr, vad_model=vad_model, tts=tts)
            llm_prewarm_task: asyncio.Task[bool] | None = None
            async with asyncio.TaskGroup() as prewarm_group:
                prewarm_group.create_task(asr.prewarm())
                prewarm_group.create_task(tts.prewarm())
                if resolved_settings.llm_prewarm:
                    llm_prewarm_task = prewarm_group.create_task(
                        _prewarm_llm(default_llm, resolved_settings.llm_prewarm_seconds)
                    )
            llm_prewarmed = llm_prewarm_task.result() if llm_prewarm_task is not None else True
            readiness.register(lambda: _healthy("asr"))
            readiness.register(lambda: _healthy("vad"))
            readiness.register(lambda: _healthy("tts"))
            readiness.register(
                _LlmReadinessProbe(
                    default_llm,
                    resolved_settings.llm_prewarm_seconds,
                    prewarmed=llm_prewarmed,
                )
            )
        manager = ManagerClient(resolved_settings)
        runtime["manager"] = manager
        if resolved_settings.require_device_auth:
            readiness.register(lambda: _manager_health(manager))
        logger.info(
            "voice_server_started",
            environment=resolved_settings.environment,
            bind_host=resolved_settings.host,
            bind_port=resolved_settings.port,
        )
        yield
        await asyncio.gather(
            *(candidate.provider.close() for candidate in llm_registry.values())
        )
        await manager.close()
        logger.info("voice_server_stopped")

    application = FastAPI(
        title="Veetee Voice Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.readiness = readiness
    application.state.runtime = runtime
    application.state.engine_factory = engine_factory
    application.state.device_sessions = device_sessions

    @application.websocket(resolved_settings.websocket_path)
    async def websocket_voice(websocket: WebSocket) -> None:
        profile = SessionProfile.defaults(resolved_settings)
        manager_device_id: str | None = None
        if resolved_settings.require_device_auth:
            protocol_version = websocket.headers.get("protocol-version")
            hardware_id = websocket.headers.get("device-id")
            client_id = websocket.headers.get("client-id")
            authorization = websocket.headers.get("authorization", "")
            has_device_token = (
                authorization.startswith("Bearer ")
                and 8 <= len(authorization) <= 264
                and authorization[7:].isascii()
            )
            if (
                protocol_version != "1"
                or not _valid_device_header(hardware_id)
                or not _valid_device_header(client_id)
                or not has_device_token
            ):
                await websocket.close(code=1008, reason="device authentication required")
                return
            assert hardware_id is not None
            manager = cast(ManagerClient, runtime["manager"])
            try:
                device = await manager.authenticate_device(hardware_id, authorization[7:])
                profile = await manager.session_profile(device)
                manager_device_id = device.device_id
            except (ManagerAuthenticationError, httpx.HTTPError, KeyError, ValueError):
                await websocket.close(code=1008, reason="device authentication failed")
                return
        session = VoiceSession(
            websocket,
            settings=resolved_settings,
            profile=profile,
            asr=cast(SherpaZipformerAsrProvider, runtime["asr"]),
            vad_model=cast(SileroVadModel, runtime["vad_model"]),
            tts=cast(VieNeuTtsProvider, runtime["tts"]),
            telemetry=(
                ConversationTelemetryBuffer(
                    cast(ManagerClient, runtime["manager"]),
                    manager_device_id,
                    queue_capacity=resolved_settings.telemetry_queue_capacity,
                    batch_size=resolved_settings.telemetry_batch_size,
                    flush_seconds=resolved_settings.telemetry_flush_seconds,
                    shutdown_seconds=resolved_settings.telemetry_shutdown_seconds,
                )
                if manager_device_id is not None
                else None
            ),
            engine_factory=engine_factory,
        )
        registration_id: str | None = None
        session_task = asyncio.create_task(session.run())
        ready_task: asyncio.Task[bool] | None = None
        registered_device_id: str | None = None
        if manager_device_id is not None:
            registered_device_id = manager_device_id
            ready_task = asyncio.create_task(session.mcp_ready.wait())
        try:
            if ready_task is not None:
                done, _ = await asyncio.wait(
                    {session_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if ready_task in done and ready_task.result():
                    assert registered_device_id is not None
                    registration_id = await device_sessions.register(
                        registered_device_id, session.mcp
                    )
            await session_task
        finally:
            if ready_task is not None and not ready_task.done():
                ready_task.cancel()
                await asyncio.gather(ready_task, return_exceptions=True)
            if registered_device_id is not None and registration_id is not None:
                await device_sessions.unregister(registered_device_id, registration_id)

    if resolved_settings.websocket_path != "/veetee/v1/":
        application.add_api_websocket_route("/veetee/v1/", websocket_voice)

    @application.websocket(resolved_settings.lab_websocket_path)
    async def websocket_lab(websocket: WebSocket) -> None:
        nonlocal active_lab_sessions
        origin = websocket.headers.get("origin")
        if not _lab_origin_allowed(origin, resolved_settings.lab_allowed_origins):
            await websocket.close(code=1008, reason="Lab origin is not allowed")
            return
        async with lab_capacity_lock:
            if active_lab_sessions >= resolved_settings.lab_max_sessions:
                await websocket.close(code=1013, reason="Lab capacity is full")
                return
            active_lab_sessions += 1
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive(), timeout=resolved_settings.hello_timeout_seconds
            )
            token = _parse_lab_auth(auth_message)
            manager = cast(ManagerClient, runtime["manager"])
            lab_context = await manager.consume_lab_session(token)
            profile = await manager.session_profile(
                DeviceContext(
                    device_id=f"lab:{lab_context.session_id}",
                    tenant_id=lab_context.tenant_id,
                    agent_id=lab_context.agent_id,
                    config_version=lab_context.config_version,
                )
            )
            if lab_context.mcp_mode == "simulated":
                tool_broker: ToolBroker = SimulatedLabToolBroker()
            elif lab_context.mcp_mode == "disabled":
                tool_broker = EmptyLabToolBroker()
            else:
                if lab_context.device_id is None:
                    raise ValueError("Selected-device Lab session is missing device id")
                catalog = await device_sessions.regular_tools(lab_context.device_id)
                tool_broker = SelectedDeviceLabToolBroker(
                    device_sessions, lab_context.device_id, catalog
                )
            session = LabSession(
                websocket,
                settings=resolved_settings,
                context=lab_context,
                profile=profile,
                asr=cast(SherpaZipformerAsrProvider, runtime["asr"]),
                vad_model=cast(SileroVadModel, runtime["vad_model"]),
                tts=cast(VieNeuTtsProvider, runtime["tts"]),
                tool_broker=tool_broker,
                engine_factory=engine_factory,
            )
            await session.run()
        except TimeoutError:
            await websocket.close(code=1008, reason="Lab authentication timeout")
        except (ManagerAuthenticationError, httpx.HTTPError, KeyError, ValueError):
            await websocket.close(code=1008, reason="Lab authentication failed")
        except DeviceSessionUnavailableError:
            await websocket.close(code=1013, reason="Selected device is not connected")
        finally:
            async with lab_capacity_lock:
                active_lab_sessions = max(0, active_lab_sessions - 1)

    if resolved_settings.lab_websocket_path != "/veetee/lab/v1/":
        application.add_api_websocket_route("/veetee/lab/v1/", websocket_lab)

    @application.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response: Response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    @application.get("/health/live", response_model=HealthResponse)
    async def live() -> HealthResponse:
        return HealthResponse(status="ok", service="voice-server")

    @application.get(
        "/health/ready",
        response_model=ReadyResponse,
        responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadyResponse}},
    )
    async def ready(response: Response) -> ReadyResponse:
        is_ready, components = await readiness.snapshot()
        if not is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(
            status="ready" if is_ready else "not_ready",
            components=[
                {
                    "name": component.name,
                    "healthy": component.healthy,
                    "required": component.required,
                    **({"detail": component.detail} if component.detail else {}),
                }
                for component in components
            ],
        )

    @application.get("/internal/v1/devices/{device_id}/mcp/tools")
    async def manager_device_tools(
        device_id: str,
        authorization: str = Header(default=""),
    ) -> list[dict[str, Any]]:
        _require_internal_service(authorization, resolved_settings.manager_internal_token)
        try:
            return await device_sessions.tools(device_id, timeout_seconds=8.0)
        except DeviceSessionUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (DeviceMcpError, OperationDeadlineExceededError) as error:
            raise HTTPException(status_code=502, detail="Device MCP catalog unavailable") from error

    @application.post("/internal/v1/devices/{device_id}/mcp/tools/{tool_name:path}/call")
    async def manager_device_tool_call(
        device_id: str,
        tool_name: str,
        payload: ManagerMcpCallRequest,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        _require_internal_service(authorization, resolved_settings.manager_internal_token)
        try:
            result = await device_sessions.call(
                device_id,
                tool_name,
                payload.arguments,
                confirmed=payload.confirmed,
                timeout_seconds=payload.timeout_seconds,
            )
        except DeviceSessionUnavailableError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Device MCP tool not found") from error
        except OperationDeadlineExceededError as error:
            raise HTTPException(status_code=504, detail="Device MCP call timed out") from error
        except DeviceMcpError as error:
            raise HTTPException(status_code=422, detail="Device MCP call failed") from error
        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="Device MCP result is invalid")
        return result

    return application


def _require_internal_service(authorization: str, expected_token: str) -> None:
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected_token or not supplied or not hmac.compare_digest(supplied, expected_token):
        raise HTTPException(status_code=401, detail="Internal service authentication failed")


async def _healthy(name: str) -> ComponentHealth:
    return ComponentHealth(name, healthy=True, required=True)


async def _manager_health(manager: ManagerClient) -> ComponentHealth:
    healthy = await manager.health()
    return ComponentHealth(
        "manager-api",
        healthy=healthy,
        required=True,
        detail=None if healthy else "unreachable",
    )


def _llm_context(operation: str, timeout_seconds: float) -> OperationContext:
    return OperationContext(
        session_id="voice-server",
        turn_id=f"voice-server:{operation}",
        generation=0,
        token=CancellationToken(),
        deadline_at=monotonic() + timeout_seconds,
    )


async def _prewarm_llm(provider: FailoverLlmProvider, timeout_seconds: float) -> bool:
    try:
        await provider.prewarm(_llm_context("llm-prewarm", timeout_seconds))
        logger.info("llm_prewarm_complete")
        return True
    except Exception as error:
        logger.warning("llm_prewarm_failed", error=type(error).__name__)
        return False


class _LlmReadinessProbe:
    def __init__(
        self,
        provider: FailoverLlmProvider,
        timeout_seconds: float,
        *,
        prewarmed: bool,
    ) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._prewarmed = prewarmed
        self._lock = asyncio.Lock()

    async def __call__(self) -> ComponentHealth:
        if not self._prewarmed:
            async with self._lock:
                if not self._prewarmed:
                    self._prewarmed = await _prewarm_llm(self._provider, self._timeout_seconds)
            if not self._prewarmed:
                return ComponentHealth("llm", healthy=False, required=True, detail="prewarm_failed")
        return await _llm_health(self._provider, self._timeout_seconds)


async def _llm_health(
    provider: FailoverLlmProvider,
    timeout_seconds: float,
) -> ComponentHealth:
    try:
        healthy = await provider.check_health(_llm_context("llm-health", timeout_seconds))
    except Exception:
        healthy = False
    return ComponentHealth(
        "llm",
        healthy=healthy,
        required=True,
        detail=None if healthy else "unreachable",
    )


def _valid_device_header(value: str | None) -> bool:
    return (
        value is not None
        and 4 <= len(value) <= 128
        and value == value.strip()
        and all(
            character.isascii() and (character.isalnum() or character in "-_.:")
            for character in value
        )
    )


def _lab_origin_allowed(origin: str | None, configured: str) -> bool:
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return origin is not None and origin in allowed


def _parse_lab_auth(message: Mapping[str, Any]) -> str:
    if message.get("type") == "websocket.disconnect":
        raise ManagerAuthenticationError("Lab disconnected before authentication")
    raw = message.get("text")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4_096:
        raise ManagerAuthenticationError("Lab authentication frame is invalid")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ManagerAuthenticationError("Lab authentication frame is invalid") from error
    token = payload.get("token") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "lab.auth"
        or not isinstance(token, str)
        or not 64 <= len(token) <= 2_048
        or not token.isascii()
    ):
        raise ManagerAuthenticationError("Lab authentication frame is invalid")
    return token


app = create_app()
