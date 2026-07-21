from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Request, Response, WebSocket, status
from pydantic import BaseModel

from veetee_voice_server.config import Settings, get_settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.logging import configure_logging
from veetee_voice_server.manager import (
    ManagerAuthenticationError,
    ManagerClient,
    SessionProfile,
)
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider
from veetee_voice_server.providers.nine_router import NineRouterLlmProvider
from veetee_voice_server.providers.semantic import JsonPlannerProvider, LocalAdmissionProvider
from veetee_voice_server.providers.silero_vad import SileroVadModel
from veetee_voice_server.providers.tools import RegistryToolBroker
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
    ) -> ConversationEngine:
        asr_llm = llm_for_profile(profile)
        asr_tts = runtime.get("tts")
        if not isinstance(asr_tts, VieNeuTtsProvider):
            raise RuntimeError("voice runtime is not ready")

        async def planner_json(
            payload: dict[str, object], context: OperationContext
        ) -> dict[str, Any]:
            return await asr_llm.complete_json(
                system_prompt=(
                    f"{profile.persona}\n\n"
                    "Return one JSON object. action must be one of respond, "
                    "call_tool_then_respond, ask_clarification, execute_pending_tool, "
                    "cancel_pending_tool, end_session, noop. dialogue_act must be one of "
                    "question, command, follow_up, answer, confirmation, denial, correction, "
                    "clarification_answer, social, interrupt, end. Include locale, intent, "
                    "response_required, response_text and optional tool_call {name, arguments}. "
                    "Set response_text only for clarification or end_session."
                ).strip(),
                user_prompt=str(payload["transcript"]),
                context=context,
            )

        planner = JsonPlannerProvider(planner_json, locale=profile.locale)
        return ConversationEngine(
            arbiter=arbiter,
            admission=LocalAdmissionProvider(),
            planner=planner,
            llm=asr_llm,
            tts=asr_tts,
            tools=RegistryToolBroker(),
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
            llm_for_profile(SessionProfile.defaults(resolved_settings))
            runtime.update(asr=asr, vad_model=vad_model, tts=tts)
            await asyncio.gather(asr.prewarm(), tts.prewarm())
            readiness.register(lambda: _healthy("asr"))
            readiness.register(lambda: _healthy("vad"))
            readiness.register(lambda: _healthy("tts"))
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
            authorization = websocket.headers.get("authorization", "")
            has_device_token = authorization.startswith("Bearer ")
            if protocol_version != "1" or not hardware_id or not has_device_token:
                await websocket.close(code=1008, reason="device authentication required")
                return
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


app = create_app()
