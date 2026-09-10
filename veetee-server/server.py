import asyncio
import os
import sys
import signal
import logging
import websockets

from config.settings import load_settings, AppConfig
from core.providers.asr.parakeet_silero import ParakeetSileroASR
from core.providers.tts.vieneu_local import VieneuLocalTTS
from core.providers.llm.omniroute_groq import OmnirouteGroqLLM
from core.providers.llm.groq_direct import build_engine_from_config
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
        logger.info(f"Loaded config: LLM provider={config.llm.provider}, model={config.llm.model}, max_tokens={config.llm.max_tokens}")
        
        # 1. Initialize Vieneu Neural TTS
        self.tts_engine = VieneuLocalTTS(
            voice=config.tts.voice,
            source_voice=config.tts.source_voice,
            sample_rate=config.tts.sample_rate,
            frame_duration_ms=config.tts.frame_duration_ms,
            stream_queue_max_chunks=config.tts.stream_queue_max_chunks,
            denoise=config.tts.denoise,
            temperature=config.tts.temperature
        )
        
        # 2. Initialize LLM (Groq direct quota-aware pool by default).
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
        self.response_audio_cache = ResponseAudioCache(self.tts_engine, config.tts)
        self.recent_turn_store = TurnTraceStore(max_recent=100)
        self.runtime_readiness = {
            "llm_warm": False,
            "asr_ready": False,
            "error_fallback_ready": False,
            "error_fallback_provenance": "",
        }
        self._readiness_repair_task: asyncio.Task | None = None
        
        self.active_sessions = {}
        self.http_server = HttpServer(
            self.config,
            self.active_sessions,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            recent_turn_store=self.recent_turn_store,
            runtime_readiness_ref=self.runtime_readiness,
        )

    async def handle_ws_connection(self, websocket: websockets.ServerConnection):
        session = ClientSession(
            websocket=websocket,
            app_config=self.config,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            turn_trace_store=self.recent_turn_store,
        )
        await session.initialize()
        self.active_sessions[session.session_id] = session
        
        try:
            async for message in websocket:
                await session.handle_message(message)
        except websockets.ConnectionClosed:
            logger.info(f"WebSocket disconnected ({session.session_id})")
        except Exception as e:
            logger.error(f"Error in connection loop: {e}", exc_info=True)
        finally:
            await session.close()
            self.active_sessions.pop(session.session_id, None)

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

    async def _repair_readiness_assets(self):
        """Retry non-fatal warm assets in background with bounded backoff."""
        delay = 1.0
        while True:
            try:
                healthy = True
                if not self.runtime_readiness.get("llm_warm"):
                    warmup = getattr(self.llm_engine, "warmup", None)
                    if warmup is None:
                        self.runtime_readiness["llm_warm"] = True
                    else:
                        try:
                            await warmup()
                            self.runtime_readiness["llm_warm"] = True
                        except Exception as exc:
                            healthy = False
                            logger.warning("LLM warmup retry failed: %s", exc)
                if not self.runtime_readiness.get("error_fallback_ready"):
                    healthy = await self._prewarm_error_fallback() and healthy
                if healthy:
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
            await ParakeetSileroASR.preload(
                self.config.asr.model,
                self.config.asr.device,
            )
            self.runtime_readiness["asr_ready"] = True
        else:
            self.runtime_readiness["asr_ready"] = True

        # 1. Start HTTP & OTA server
        await self.http_server.start()

        if not all(self.runtime_readiness.values()):
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
                ping_interval=None
            ):
                logger.info("=" * 60)
                logger.info("  🚀 VeeTee Realtime Server is READY (Local Non-Docker)")
                logger.info(f"  • WebSocket URL: ws://{self.local_ip}:{ws_port}/")
                logger.info(f"  • OTA URL:       http://{self.local_ip}:{self.config.server.http_port}/ota/")
                logger.info(f"  • Web Dashboard: http://{self.local_ip}:{self.config.server.http_port}/")
                logger.info("=" * 60)
                
                stop_event = asyncio.Event()
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, stop_event.set)
                    except (NotImplementedError, RuntimeError, ValueError):
                        pass
                try:
                    await stop_event.wait()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass
        finally:
            if self._readiness_repair_task is not None:
                self._readiness_repair_task.cancel()
                await asyncio.gather(self._readiness_repair_task, return_exceptions=True)
                self._readiness_repair_task = None
            await self.response_audio_cache.shutdown()
            close_llm = getattr(self.llm_engine, "close", None)
            if close_llm is not None:
                await close_llm()

async def main():
    config = load_settings()
    log_level = getattr(logging, str(config.server.log_level).upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    server = VeeTeeServer(config)
    await server.start()

if __name__ == "__main__":
    asyncio.run(main())
