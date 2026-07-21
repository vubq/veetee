from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response, WebSocket, status
from pydantic import BaseModel

from veetee_voice_server.config import Settings, get_settings
from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.conversation.types import ConversationPolicy
from veetee_voice_server.logging import configure_logging
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

    async def planner_json(payload: dict[str, object], context: OperationContext) -> dict[str, Any]:
        provider = runtime["llm"]
        assert isinstance(provider, NineRouterLlmProvider)
        return await provider.complete_json(
            system_prompt=(
                "Return one JSON object. action must be one of respond, "
                "call_tool_then_respond, ask_clarification, execute_pending_tool, "
                "cancel_pending_tool, end_session, noop. dialogue_act must be one of "
                "question, command, follow_up, answer, confirmation, denial, correction, "
                "clarification_answer, social, interrupt, end. Include locale, intent, "
                "response_required, response_text and optional tool_call {name, arguments}. "
                "Set response_text only for clarification or end_session."
            ),
            user_prompt=str(payload["transcript"]),
            context=context,
        )

    def engine_factory(arbiter: TurnArbiter, sink: ConversationSink) -> ConversationEngine:
        asr_llm = runtime.get("llm")
        asr_tts = runtime.get("tts")
        if not isinstance(asr_llm, NineRouterLlmProvider) or not isinstance(
            asr_tts, VieNeuTtsProvider
        ):
            raise RuntimeError("voice runtime is not ready")
        planner = JsonPlannerProvider(planner_json, locale=resolved_settings.default_locale)
        return ConversationEngine(
            arbiter=arbiter,
            admission=LocalAdmissionProvider(),
            planner=planner,
            llm=asr_llm,
            tts=asr_tts,
            tools=RegistryToolBroker(),
            sink=sink,
            policy=ConversationPolicy(
                first_input_seconds=resolved_settings.first_input_seconds,
                between_turns_seconds=resolved_settings.between_turns_seconds,
                closing_grace_seconds=resolved_settings.closing_grace_seconds,
                total_turn_seconds=30.0,
                admission_seconds=1.0,
                planner_seconds=4.0,
                llm_seconds=20.0,
                tts_seconds=10.0,
                mcp_seconds=10.0,
            ),
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
            llm = NineRouterLlmProvider(
                base_url=str(resolved_settings.nine_router_base_url),
                model=resolved_settings.nine_router_model,
                api_key=resolved_settings.nine_router_api_key,
                reasoning_effort=resolved_settings.nine_router_reasoning_effort,
            )
            runtime.update(asr=asr, vad_model=vad_model, tts=tts, llm=llm)
            await asyncio.gather(asr.prewarm(), tts.prewarm())
            readiness.register(lambda: _healthy("asr"))
            readiness.register(lambda: _healthy("vad"))
            readiness.register(lambda: _healthy("tts"))
        logger.info(
            "voice_server_started",
            environment=resolved_settings.environment,
            bind_host=resolved_settings.host,
            bind_port=resolved_settings.port,
        )
        yield
        llm_runtime = runtime.get("llm")
        if isinstance(llm_runtime, NineRouterLlmProvider):
            await llm_runtime.close()
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
        session = VoiceSession(
            websocket,
            settings=resolved_settings,
            asr=cast(SherpaZipformerAsrProvider, runtime["asr"]),
            vad_model=cast(SileroVadModel, runtime["vad_model"]),
            tts=cast(VieNeuTtsProvider, runtime["tts"]),
            engine_factory=engine_factory,
        )
        await session.run()

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


app = create_app()
