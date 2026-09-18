import asyncio
import os
import sys
import signal
import logging
from http import HTTPStatus
from core.access import WebSocketAccess, serve_session
from core.assistant_runtime import build_assistant_llm_view, build_assistant_tts_view
from core.management_store import ManagementStore
import websockets

from config.settings import load_settings, AppConfig
from core.providers.asr.parakeet_silero import ParakeetSileroASR
from core.providers.tts.vieneu_local import VieneuLocalTTS
from core.providers.llm.omniroute_groq import OmnirouteGroqLLM
from core.providers.llm.groq_direct import build_engine_from_config
from core.providers.llm.unavailable import UnavailableLLM
from core.response_audio_cache import ResponseAudioCache
from core.session import ClientSession
from core.turn_metrics import TurnTraceStore
from http_server import HttpServer, get_local_ip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("VeeTeeServer")

class VeeTeeServer:
    def __init__(self, config: AppConfig):
        self.config = config
        self.local_ip = get_local_ip()
        server_dir = os.path.dirname(os.path.abspath(__file__))
        self.management_store = ManagementStore(os.path.join(server_dir, "data", "manager-state.json"))
        runtime = self.management_store.runtime_raw()
        # Persisted manager values survive deploys. Secrets are materialized only
        # into this process and are never returned unmasked by the API.
        for key, value in runtime.items():
            if key.startswith(("GROQ_API_KEY_", "DEEPGRAM_API_KEY", "HF_TOKEN")):
                os.environ[key] = str(value)
        if runtime.get("llm.model"):
            config.llm.model = str(runtime["llm.model"])
        if runtime.get("tts.voice"):
            config.tts.voice = str(runtime["tts.voice"])
        if runtime.get("asr.device"):
            config.asr.device = str(runtime["asr.device"])
        logger.info(f"Loaded config: LLM provider={config.llm.provider}, model={config.llm.model}, max_tokens={config.llm.max_tokens}")
        
        # 1. Initialize Vieneu Neural TTS (saved dashboard voice wins).
        self.tts_engine = VieneuLocalTTS(
            voice=config.tts.voice,
            voice_state_path=os.path.join(server_dir, "data", "voice.txt"),
            source_voice=config.tts.source_voice,
            sample_rate=config.tts.sample_rate,
            frame_duration_ms=config.tts.frame_duration_ms,
            stream_queue_max_chunks=config.tts.stream_queue_max_chunks,
            denoise=config.tts.denoise,
            temperature=config.tts.temperature
        )
        
        # 2. Initialize LLM. Management/OTA must remain available even when
        # provider credentials are missing or malformed so an operator can
        # repair runtime configuration from the dashboard.
        try:
            if config.llm.provider == "groq":
                self.llm_engine = build_engine_from_config(
                    config.llm, server_dir=server_dir)
            else:
                logger.info("Using legacy OmniRoute LLM provider")
                self.llm_engine = OmnirouteGroqLLM(
                    base_url=config.llm.base_url,
                    api_key=config.llm.api_key,
                    model=config.llm.model,
                    temperature=config.llm.temperature,
                    max_tokens=config.llm.max_tokens,
                    reasoning_format=config.llm.reasoning_format,
                    base_prompt=config.llm.base_prompt,
                    prompt_template_path=os.path.join(server_dir, config.llm.prompt_template),
                    base_prompt_state_path=os.path.join(server_dir, "data", "base-prompt.txt"),
                )
        except Exception as exc:
            logger.error("LLM initialization unavailable; starting management plane degraded: %s", exc)
            self.llm_engine = UnavailableLLM(
                model=config.llm.model,
                extra_models=config.llm.extra_models,
                reason=f"llm_configuration_unavailable: {type(exc).__name__}",
            )
        self.response_audio_cache = ResponseAudioCache(self.tts_engine, config.tts)
        self.recent_turn_store = TurnTraceStore(max_recent=100)
        self.runtime_readiness = {
            "llm_warm": False,
            "llm_error": getattr(self.llm_engine, "reason", ""),
            "llm_retryable": not bool(getattr(self.llm_engine, "permanent_unavailable", False)),
            "asr_ready": False,
            "asr_error": "",
            "error_fallback_ready": False,
            "error_fallback_provenance": "",
        }
        self._readiness_repair_task: asyncio.Task | None = None
        
        self.active_sessions = {}
        self.websocket_access = WebSocketAccess(config.server, config.management, self.management_store)
        self.http_server = HttpServer(
            self.config,
            self.active_sessions,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            recent_turn_store=self.recent_turn_store,
            runtime_readiness_ref=self.runtime_readiness,
            websocket_access=self.websocket_access,
            management_store=self.management_store,
        )

    def _session_engines(self, auth):
        assistant = (auth or {}).get("assistant") if isinstance(auth, dict) else None
        return (
            build_assistant_tts_view(self.tts_engine, assistant),
            build_assistant_llm_view(self.llm_engine, assistant, self.config),
            assistant,
        )

    async def handle_ws_connection(self, websocket: websockets.ServerConnection):
        access = self.websocket_access
        auth = access.authenticate(websocket.request.headers)
        if not access.origin_allowed(websocket.request.headers) or not auth:
            await websocket.close(code=1008, reason="unauthorized")
            return
        session_tts, session_llm, assistant = self._session_engines(auth)
        auth_device = auth.get("device") if isinstance(auth, dict) else None
        owner_id = self.config.memory.trusted_owner_id
        if isinstance(auth_device, dict) and str(auth_device.get("owner_id") or "").strip():
            owner_id = str(auth_device["owner_id"]).strip()
        def factory():
            return ClientSession(
                websocket=websocket,
                app_config=self.config,
                tts_engine=session_tts,
                llm_engine=session_llm,
                response_audio_cache=self.response_audio_cache if assistant is None else None,
                turn_trace_store=self.recent_turn_store,
                authenticated_owner_id=owner_id,
            )
        try:
            await serve_session(websocket, access, self.config, self.active_sessions, factory)
        except websockets.ConnectionClosed:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"Error in connection loop: {e}", exc_info=True)

    def authorize_ws(self, connection, request):
        if not self.websocket_access.origin_allowed(request.headers):
            return connection.respond(HTTPStatus.FORBIDDEN, "Origin denied\n")
        if not self.websocket_access.authenticated(request.headers):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Authentication required\n")
        if self.websocket_access.active >= self.config.server.ws_max_sessions:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Session limit\n")

    async def _prewarm_error_fallback(self) -> bool:
        """Prepare one AI-authored cached recovery clip used by failed turns.

        Runtime error handling is cached-only so a failed TTS engine is never
        called recursively while attempting to explain that same failure.
        """
        try:
            try:
                recovery = await self.response_audio_cache.get_recovery()
            except Exception:
                recovery = None
            if recovery is not None:
                provenance = recovery.provenance
                self.runtime_readiness["error_fallback_ready"] = True
                self.runtime_readiness["error_fallback_provenance"] = provenance
                return True

            pending = await self.response_audio_cache.get_pending_recovery()
            if pending is not None:
                cleaned, provenance = pending
            else:
                generator = getattr(self.llm_engine, "generate_recovery_message", None)
                if generator is None:
                    raise RuntimeError("LLM provider has no recovery-message generator")
                text = await asyncio.wait_for(
                    generator(),
                    timeout=max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0),
                )
                cleaned = str(text or "").strip()
                if not cleaned:
                    raise RuntimeError("LLM returned no recovery message")
                provenance = f"ai:{self.config.llm.model}"
            await self.response_audio_cache.prepare_recovery(
                cleaned,
                self.config.conversation.fixed_response_timeout_seconds,
                provenance=provenance,
            )
        except Exception as exc:
            self.runtime_readiness["error_fallback_ready"] = False
            self.runtime_readiness["error_fallback_provenance"] = ""
            logger.warning("AI recovery audio prewarm unavailable: %s", exc)
            return False
        self.runtime_readiness["error_fallback_ready"] = True
        self.runtime_readiness["error_fallback_provenance"] = provenance
        logger.info("AI recovery audio is ready provenance=%s", provenance)
        return True

    @staticmethod
    def _llm_error_retryable(exc: Exception) -> bool:
        text = str(exc or "").lower()
        permanent_markers = (
            "invalid api key",
            "invalid_api_key",
            "key pool is empty",
            "no enabled groq key",
            "authentication",
            "unauthorized",
            "model_not_found",
            "unknown model",
        )
        return not any(marker in text for marker in permanent_markers)

    async def _repair_readiness_assets(self):
        """Retry transient LLM warmup failures with bounded backoff."""
        if not self.runtime_readiness.get("llm_retryable", True):
            return
        delay = 1.0
        while True:
            try:
                if self.runtime_readiness.get("llm_warm"):
                    return
                if not self.runtime_readiness.get("llm_retryable", True):
                    return
                warmup = getattr(self.llm_engine, "warmup", None)
                if warmup is None:
                    self.runtime_readiness["llm_warm"] = True
                    return
                try:
                    await warmup()
                    self.runtime_readiness["llm_warm"] = True
                    return
                except Exception as exc:
                    self.runtime_readiness["llm_error"] = str(exc)
                    retryable = self._llm_error_retryable(exc)
                    self.runtime_readiness["llm_retryable"] = retryable
                    logger.warning("LLM warmup retry failed: %s", exc)
                    if not retryable:
                        return
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Readiness asset retry failed: %s", exc)
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2.0)

    async def start(self):
        # Warm the persistent LLM HTTP connection before accepting user turns.
        warmup = getattr(self.llm_engine, "warmup", None)
        if warmup is not None:
            try:
                await warmup()
                self.runtime_readiness["llm_warm"] = True
            except Exception as exc:
                self.runtime_readiness["llm_error"] = str(exc)
                self.runtime_readiness["llm_retryable"] = (
                    not bool(getattr(self.llm_engine, "permanent_unavailable", False))
                    and self._llm_error_retryable(exc)
                )
                logger.warning("LLM warmup failed; server will start degraded: %s", exc)
        else:
            self.runtime_readiness["llm_warm"] = True

        await self._prewarm_error_fallback()

        # Parakeet is a large local model. Load it before opening the web/WS
        # listeners so the first microphone connection cannot time out while
        # waiting for a cold model restore.
        if self.config.asr.provider.strip().lower() in {
            "parakeet_silero",
            "parakeet",
            "silero_parakeet",
        }:
            logger.info("Preloading Parakeet ASR before accepting clients...")
            try:
                await ParakeetSileroASR.preload(
                    self.config.asr.model,
                    self.config.asr.device,
                )
                self.runtime_readiness["asr_ready"] = True
            except Exception as exc:
                self.runtime_readiness["asr_ready"] = False
                self.runtime_readiness["asr_error"] = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "ASR preload unavailable; starting management plane degraded: %s",
                    exc,
                )
        else:
            self.runtime_readiness["asr_ready"] = True

        # 1. Start HTTP & OTA server
        await self.http_server.start()

        if not self.runtime_readiness.get("llm_warm"):
            self._readiness_repair_task = asyncio.create_task(
                self._repair_readiness_assets(),
                name="veetee-readiness-repair",
            )

        # 2. Start WebSocket server
        host = self.config.server.host
        ws_port = self.config.server.ws_port
        logger.info(f"WebSocket Server listening on ws://{self.local_ip}:{ws_port}")
        
        try:
            async with websockets.serve(
                self.handle_ws_connection,
                host,
                ws_port,
                ping_interval=30,
                process_request=self.authorize_ws,
                max_size=65536,
            ):
                logger.info("=" * 60)
                logger.info("  🚀 VeeTee Realtime Server is READY (Local Non-Docker)")
                logger.info(f"  • WebSocket URL: ws://{self.local_ip}:{ws_port}/")
                logger.info(f"  • OTA URL:       http://{self.local_ip}:{self.config.server.http_port}/ota/")
                logger.info(f"  • Web Dashboard: http://{self.local_ip}:{self.config.server.http_port}/")
                logger.info("=" * 60)
                
                stop_event = asyncio.Event()
                loop = asyncio.get_running_loop()
                registered_signals = []
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, stop_event.set)
                        registered_signals.append(sig)
                    except (NotImplementedError, RuntimeError, ValueError):
                        pass
                try:
                    await stop_event.wait()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass
                finally:
                    for sig in registered_signals:
                        try:
                            loop.remove_signal_handler(sig)
                        except (NotImplementedError, RuntimeError, ValueError):
                            pass
        finally:
            if self._readiness_repair_task is not None:
                self._readiness_repair_task.cancel()
                await asyncio.gather(self._readiness_repair_task, return_exceptions=True)
                self._readiness_repair_task = None

            # Close both standalone-WS and aiohttp-/ws sessions before
            # tearing down their listener. Session.close() is idempotent, so
            # connection-handler finalizers may race this safely.
            sessions = list(self.active_sessions.values())
            if sessions:
                await asyncio.gather(
                    *(session.close() for session in sessions),
                    return_exceptions=True,
                )
                self.active_sessions.clear()
            try:
                await self.http_server.stop()
            except Exception as exc:
                logger.warning("HTTP server cleanup failed: %s", exc)
            try:
                await self.response_audio_cache.shutdown()
            except Exception as exc:
                logger.warning("Response audio cache cleanup failed: %s", exc)
            shutdown_tts = getattr(self.tts_engine, "shutdown", None)
            if shutdown_tts is not None:
                try:
                    await shutdown_tts()
                except Exception as exc:
                    logger.warning("TTS cleanup failed: %s", exc)
            close_llm = getattr(self.llm_engine, "close", None)
            if close_llm is not None:
                try:
                    await close_llm()
                except Exception as exc:
                    logger.warning("LLM cleanup failed: %s", exc)

async def main():
    config = load_settings()
    log_level = getattr(logging, str(config.server.log_level).upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    server = VeeTeeServer(config)
    await server.start()

if __name__ == "__main__":
    asyncio.run(main())
