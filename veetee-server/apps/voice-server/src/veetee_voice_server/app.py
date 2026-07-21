from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Header, HTTPException, Request, Response, WebSocket, status
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
    ManagerAuthenticationError,
    ManagerClient,
    SessionProfile,
)
from veetee_voice_server.providers.contracts import ToolBroker
from veetee_voice_server.providers.failover import (
    FailoverLlmProvider,
    LlmProviderCandidate,
)
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider
from veetee_voice_server.providers.semantic import StructuredConversationGate
from veetee_voice_server.providers.silero_vad import SileroVadModel
from veetee_voice_server.readiness import ComponentHealth, ReadinessRegistry
from veetee_voice_server.telemetry import ConversationTelemetryBuffer
from veetee_voice_server.transport.mcp import DeviceMcpError
from veetee_voice_server.transport.session import VoiceSession
from veetee_voice_server.transport.session_registry import (
    DeviceSessionRegistry,
    DeviceSessionUnavailableError,
)
from veetee_voice_server.transport.sink import ConversationSink

logger = structlog.get_logger(__name__)


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
    catalog = json.dumps(tools.list_tools(), ensure_ascii=False, separators=(",", ":"))
    return (
        "Return one JSON object with admission, dialogue_act and plan. admission.decision "
        "must be accepted, non_actionable, not_addressed, unclear, interrupt or end; "
        "include confidence, addressed_to_robot and one bounded reason_code. Decide whether "
        "the transcript is intentional, relevant and directed to this assistant before "
        "planning a response or tool. Do not infer a named environmental source or answer "
        "incidental speech. plan.action must be one of respond, "
        "call_tool_then_respond, ask_clarification, execute_pending_tool, "
        "cancel_pending_tool, end_session, noop. dialogue_act must be one of "
        "question, command, follow_up, answer, confirmation, denial, correction, "
        "clarification_answer, social, interrupt, end. plan must include locale, intent, "
        "response_required, response_text and tool_call. Use null when response_text or "
        "tool_call does not apply. "
        "For respond, ask_clarification or end_session, set response_text to one to "
        "three short directly speakable sentences without Markdown. Set response_text to null "
        "for tool actions because the final response must use the actual tool result. "
        "Only choose a tool action when its exact name exists in the available tool "
        f"catalog: {catalog}. "
        "When the catalog is empty, never invent a tool name."
        f"\n\nAgent context:\n{profile.persona}"
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
        "response_text": {"type": ["string", "null"]},
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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    readiness = ReadinessRegistry()
    runtime: dict[str, object] = {}
    llm_registry: dict[tuple[str, str, str, str, str], LlmProviderCandidate] = {}
    llm_chain_registry: dict[tuple[tuple[str, str, str, str, str], ...], FailoverLlmProvider] = {}
    device_sessions = DeviceSessionRegistry()

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
            return await asr_llm.complete_json(
                system_prompt=_planner_system_prompt(profile, tool_broker),
                user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                context=context,
                schema=_planner_output_schema(tool_broker),
                schema_name="veetee_submit_conversation_gate",
            )

        gate = StructuredConversationGate(gate_json, locale=profile.locale)
        return ConversationEngine(
            arbiter=arbiter,
            admission=gate,
            planner=gate,
            llm=asr_llm,
            tts=asr_tts,
            tools=tool_broker,
            sink=sink,
            policy=profile.policy,
            system_prompt=profile.persona or None,
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


app = create_app()
