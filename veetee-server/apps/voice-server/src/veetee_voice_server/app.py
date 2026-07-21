from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any, cast
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request, Response, WebSocket, status
from pydantic import BaseModel

from veetee_voice_server.config import Settings, get_settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.logging import configure_logging
from veetee_voice_server.manager import (
    ManagerAuthenticationError,
    ManagerClient,
    SessionProfile,
)
from veetee_voice_server.providers.contracts import ToolBroker
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider
from veetee_voice_server.providers.semantic import JsonPlannerProvider, LocalAdmissionProvider
from veetee_voice_server.providers.silero_vad import SileroVadModel
from veetee_voice_server.readiness import ComponentHealth, ReadinessRegistry
from veetee_voice_server.transport.session import VoiceSession
from veetee_voice_server.transport.sink import ConversationSink

logger = structlog.get_logger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str


class ReadyResponse(BaseModel):
    status: str
    components: list[dict[str, object]]


def _planner_system_prompt(profile: SessionProfile, tools: ToolBroker) -> str:
    catalog = json.dumps(tools.list_tools(), ensure_ascii=False, separators=(",", ":"))
    return (
        "Return one JSON object. action must be one of respond, "
        "call_tool_then_respond, ask_clarification, execute_pending_tool, "
        "cancel_pending_tool, end_session, noop. dialogue_act must be one of "
        "question, command, follow_up, answer, confirmation, denial, correction, "
        "clarification_answer, social, interrupt, end. Include locale, intent, "
        "response_required, response_text and optional tool_call {name, arguments}. "
        "For respond, ask_clarification or end_session, set response_text to one to "
        "three short directly speakable sentences without Markdown. Omit response_text "
        "for tool actions because the final response must use the actual tool result. "
        "Only choose a tool action when its exact name exists in the available tool "
        f"catalog: {catalog}. "
        "When the catalog is empty, never invent a tool name."
        f"\n\nAgent context:\n{profile.persona}"
    ).strip()


def _planner_output_schema(tools: ToolBroker) -> dict[str, object]:
    tool_names = [
        item["name"]
        for item in tools.list_tools()
        if isinstance(item.get("name"), str)
    ]
    properties: dict[str, object] = {
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
        "locale": {"type": "string"},
        "intent": {"type": "string"},
        "response_required": {"type": "boolean"},
        "response_text": {"type": "string"},
    }
    if tool_names:
        properties["tool_call"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "arguments"],
            "properties": {
                "name": {"type": "string", "enum": tool_names},
                "arguments": {"type": "object"},
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "action",
            "dialogue_act",
            "locale",
            "intent",
            "response_required",
        ],
        "properties": properties,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    readiness = ReadinessRegistry()
    runtime: dict[str, object] = {}
    llm_registry: dict[tuple[str, str, str], NineRouterLlmProvider] = {}

    def llm_for_profile(profile: SessionProfile) -> NineRouterLlmProvider:
        key = (profile.llm_base_url, profile.llm_model, profile.llm_reasoning_effort)
        provider = llm_registry.get(key)
        if provider is None:
            provider = NineRouterLlmProvider(
                base_url=profile.llm_base_url,
                model=profile.llm_model,
                api_key=resolved_settings.nine_router_api_key,
                reasoning_effort=profile.llm_reasoning_effort,
            )
            llm_registry[key] = provider
        return provider

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
        async def planner_json(
            payload: dict[str, object], context: OperationContext
        ) -> dict[str, Any]:
            return await asr_llm.complete_json(
                system_prompt=_planner_system_prompt(profile, tool_broker),
                user_prompt=str(payload["transcript"]),
                context=context,
                schema=_planner_output_schema(tool_broker),
                schema_name="veetee_submit_conversation_plan",
            )

        planner = JsonPlannerProvider(planner_json, locale=profile.locale)
        return ConversationEngine(
            arbiter=arbiter,
            admission=LocalAdmissionProvider(),
            planner=planner,
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
            llm_prewarmed = (
                llm_prewarm_task.result() if llm_prewarm_task is not None else True
            )
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
        await asyncio.gather(*(provider.close() for provider in llm_registry.values()))
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

    @application.websocket(resolved_settings.websocket_path)
    async def websocket_voice(websocket: WebSocket) -> None:
        profile = SessionProfile.defaults(resolved_settings)
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
            engine_factory=engine_factory,
        )
        await session.run()

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

    return application


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


async def _prewarm_llm(provider: NineRouterLlmProvider, timeout_seconds: float) -> bool:
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
        provider: NineRouterLlmProvider,
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
                    self._prewarmed = await _prewarm_llm(
                        self._provider, self._timeout_seconds
                    )
            if not self._prewarmed:
                return ComponentHealth(
                    "llm", healthy=False, required=True, detail="prewarm_failed"
                )
        return await _llm_health(self._provider, self._timeout_seconds)


async def _llm_health(
    provider: NineRouterLlmProvider,
    timeout_seconds: float,
) -> ComponentHealth:
    try:
        healthy = await provider.health(_llm_context("llm-health", timeout_seconds))
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
