"""FastAPI entrypoint for the M1.1 server foundation."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .app_context import request_id_context
from .audio import is_native_opus_available
from .config import (
    Settings,
    get_effective_device_websocket_url,
    get_settings,
    validate_device_websocket_url,
)
from .device_gateway import DeviceSessionRegistry
from .device_gateway import router as device_gateway_router
from .logging import configure_logging

logger = logging.getLogger("veetee.server")


def _request_id(value: str | None) -> str:
    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    logger.info("server_started", extra={"context": {"environment": settings.environment}})

    vad_runtime = getattr(app.state, "vad_runtime", None)
    if settings.vad_provider == "silero_onnx" and vad_runtime is None:
        try:
            from .pipeline.vad import SileroVADConfig, SileroVADRuntime

            cfg = SileroVADConfig(
                threshold=settings.vad_threshold,
                neg_threshold=settings.vad_neg_threshold,
                pre_roll_ms=settings.vad_pre_roll_ms,
                min_speech_ms=settings.vad_min_speech_ms,
                end_silence_ms=settings.vad_end_silence_ms,
                max_utterance_ms=settings.vad_max_utterance_ms,
                max_concurrency=settings.vad_max_concurrency,
                admission_timeout_seconds=settings.vad_admission_timeout_seconds,
            )
            vad_runtime = SileroVADRuntime(config=cfg, model_path=settings.vad_model_path)
            await vad_runtime.startup()
            app.state.vad_runtime = vad_runtime
        except Exception as exc:
            logger.error("Failed to start VAD runtime: %s", exc)

    asr_runtime = getattr(app.state, "asr_runtime", None)
    if settings.asr_provider == "pho_whisper" and asr_runtime is None:
        try:
            from .pipeline.asr import PhoWhisperConfig, PhoWhisperRuntime

            asr_cfg = PhoWhisperConfig(
                model_id=settings.asr_model_id,
                device=settings.asr_device,
                compute_type=settings.asr_compute_type,
                max_concurrency=settings.asr_max_concurrency,
                admission_timeout_seconds=settings.asr_admission_timeout_seconds,
                total_timeout_seconds=settings.asr_total_timeout_seconds,
                max_audio_seconds=settings.asr_max_audio_seconds,
                language=settings.asr_language,
                local_files_only=settings.asr_local_files_only,
            )
            asr_runtime = PhoWhisperRuntime(config=asr_cfg)
            await asr_runtime.startup()
            app.state.asr_runtime = asr_runtime
        except Exception as exc:
            logger.error("Failed to start ASR runtime: %s", exc)
            if asr_runtime is not None:
                await asr_runtime.shutdown()
                asr_runtime = None

    llm_runtime = getattr(app.state, "llm_runtime", None)
    if settings.llm_provider == "omniroute" and llm_runtime is None:
        try:
            from .pipeline.llm import OmniRouteLLMConfig, OmniRouteLLMRuntime

            llm_cfg = OmniRouteLLMConfig(
                base_url=settings.llm_omniroute_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_omniroute_model,
                reasoning_effort=settings.llm_omniroute_reasoning_effort,
                connect_timeout_seconds=settings.llm_connect_timeout_seconds,
                first_token_timeout_seconds=settings.llm_first_token_timeout_seconds,
                total_timeout_seconds=settings.llm_total_timeout_seconds,
                max_concurrency=settings.llm_max_concurrency,
                admission_timeout_seconds=settings.llm_admission_timeout_seconds,
                circuit_breaker_failure_threshold=settings.llm_circuit_breaker_failure_threshold,
                circuit_breaker_cooldown_seconds=settings.llm_circuit_breaker_cooldown_seconds,
                max_response_bytes=settings.llm_max_response_bytes,
            )
            llm_runtime = OmniRouteLLMRuntime(config=llm_cfg)
            await llm_runtime.startup()
            app.state.llm_runtime = llm_runtime
        except Exception as exc:
            logger.error("Failed to start LLM runtime: %s", exc)
            if llm_runtime is not None:
                await llm_runtime.shutdown()
                llm_runtime = None

    tts_runtime = getattr(app.state, "tts_runtime", None)
    if settings.tts_provider == "gemini" and tts_runtime is None:
        try:
            from .pipeline.tts import GeminiTTSConfig, GeminiTTSRuntime

            tts_cfg = GeminiTTSConfig(
                api_keys=settings.tts_gemini_api_keys,
                main_model=settings.tts_gemini_main_model,
                fallback_model=settings.tts_gemini_fallback_model,
                enable_fallback_model=settings.tts_enable_fallback_model,
                voice=settings.tts_gemini_voice,
                prompt_prefix=settings.tts_gemini_prompt_prefix,
                connect_timeout_seconds=settings.tts_connect_timeout_seconds,
                first_audio_timeout_seconds=settings.tts_first_audio_timeout_seconds,
                total_timeout_seconds=settings.tts_total_timeout_seconds,
                max_concurrency=settings.tts_max_concurrency,
                admission_timeout_seconds=settings.tts_admission_timeout_seconds,
                circuit_breaker_failure_threshold=settings.tts_circuit_breaker_failure_threshold,
                circuit_breaker_cooldown_seconds=settings.tts_circuit_breaker_cooldown_seconds,
                max_retry_after_seconds=settings.tts_max_retry_after_seconds,
                max_response_bytes=settings.tts_max_response_bytes,
            )
            tts_runtime = GeminiTTSRuntime(config=tts_cfg)
            await tts_runtime.startup()
            app.state.tts_runtime = tts_runtime
        except Exception as exc:
            logger.error("Failed to start TTS runtime: %s", exc)
            if tts_runtime is not None:
                await tts_runtime.shutdown()
                tts_runtime = None

    vieneu_runtime = getattr(app.state, "vieneu_runtime", None)
    if settings.tts_provider == "vieneu" and vieneu_runtime is None:
        from .pipeline.tts import VieNeuTTSRuntime

        vieneu_runtime = VieNeuTTSRuntime(
            settings.vieneu_base_url, settings.vieneu_timeout_seconds
        )
        await vieneu_runtime.startup()
        app.state.vieneu_runtime = vieneu_runtime

    # Brain AI / Mốc 3 state initialization
    if getattr(app.state, "prompt_registry", None) is None:
        from .prompt import create_default_prompt_registry

        app.state.prompt_registry = create_default_prompt_registry()

    if getattr(app.state, "tool_registry", None) is None:
        from .tools import ToolRegistry

        tr = ToolRegistry()
        app.state.tool_registry = tr

    if getattr(app.state, "tool_executor", None) is None:
        from .tools import ToolExecutor

        app.state.tool_executor = ToolExecutor(
            default_timeout_seconds=settings.tool_execution_timeout_seconds,
            max_output_chars=settings.tool_max_output_chars,
        )

    if getattr(app.state, "memory_store", None) is None:
        from .memory import InMemoryMemoryStore

        app.state.memory_store = InMemoryMemoryStore()

    if getattr(app.state, "mcp_adapter", None) is None:
        from .mcp import MCPAdapter

        app.state.mcp_adapter = MCPAdapter(
            tool_registry=app.state.tool_registry,
            tool_executor=app.state.tool_executor,
        )

    if getattr(app.state, "intent_router", None) is None:
        from .intent import IntentRouter

        app.state.intent_router = IntentRouter(default_strategy=settings.intent_strategy)

    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        registry: DeviceSessionRegistry | None = getattr(
            app.state, "device_session_registry", None
        )
        if registry is not None:
            await registry.close_all(code=1012, reason="Server shutdown")
        if vad_runtime is not None:
            await vad_runtime.shutdown()
        if asr_runtime is not None:
            await asr_runtime.shutdown()
        if llm_runtime is not None:
            await llm_runtime.shutdown()
        if tts_runtime is not None:
            await tts_runtime.shutdown()
        if vieneu_runtime is not None:
            await vieneu_runtime.shutdown()
        logger.info("server_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    app = FastAPI(title="Veetee Server API", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.state.ready = False
    app.state.device_session_registry = DeviceSessionRegistry()
    app.include_router(device_gateway_router)

    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id(request.headers.get("X-Veetee-Request-Id"))
        token = request_id_context.set(request_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled_request_error")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "code": "veetee_internal",
                        "message": "Internal server error",
                        "request_id": request_id,
                    },
                )
            response.headers["X-Veetee-Request-Id"] = request_id
            return response
        finally:
            request_id_context.reset(token)

    @app.get("/healthz", tags=["operations"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": runtime_settings.app_name}

    @app.get("/readyz", tags=["operations"])
    async def readyz() -> JSONResponse:
        if not runtime_settings.readiness_enabled:
            return JSONResponse(
                content={"status": "disabled", "service": runtime_settings.app_name}
            )
        if not app.state.ready:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        if not runtime_settings.device_gateway_token and runtime_settings.environment != "test":
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "gateway_token_not_configured"},
            )
        if runtime_settings.audio_codec == "native" and not is_native_opus_available():
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "native_opus_not_ready"},
            )
        eff_url = get_effective_device_websocket_url(runtime_settings)
        url_valid, _ = validate_device_websocket_url(eff_url)
        if not url_valid:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "invalid_websocket_public_url"},
            )
        if runtime_settings.vad_provider == "silero_onnx":
            vad_runtime = getattr(app.state, "vad_runtime", None)
            if vad_runtime is None or not vad_runtime.is_ready:
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "reason": "vad_runtime_not_ready"},
                )
        if runtime_settings.asr_provider == "pho_whisper":
            asr_runtime = getattr(app.state, "asr_runtime", None)
            if asr_runtime is None or not asr_runtime.is_ready:
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "reason": "asr_runtime_not_ready"},
                )
        if runtime_settings.llm_provider == "omniroute":
            llm_runtime = getattr(app.state, "llm_runtime", None)
            if llm_runtime is None or not llm_runtime.is_ready:
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "reason": "llm_runtime_not_ready"},
                )
        if runtime_settings.tts_provider == "gemini":
            tts_runtime = getattr(app.state, "tts_runtime", None)
            if tts_runtime is None or not tts_runtime.is_ready:
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "reason": "tts_runtime_not_ready"},
                )
        if runtime_settings.tts_provider == "vieneu":
            vieneu_runtime = getattr(app.state, "vieneu_runtime", None)
            if vieneu_runtime is None or not vieneu_runtime.is_ready:
                return JSONResponse(
                    status_code=503,
                    content={"status": "not_ready", "reason": "vieneu_runtime_not_ready"},
                )
        return JSONResponse(content={"status": "ready", "service": runtime_settings.app_name})

    return app


app = create_app()
