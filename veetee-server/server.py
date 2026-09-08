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
from core.response_audio_cache import ResponseAudioCache
from core.session import ClientSession
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
        logger.info(f"Loaded config: LLM provider=OmniRoute, model={config.llm.model}, max_tokens={config.llm.max_tokens}")
        
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
        
        # 2. Initialize LLM (Omniroute / Groq Qwen 3.6 27B)
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
        self.greeting_pool: list[str] = []
        
        self.active_sessions = {}
        self.http_server = HttpServer(
            self.config,
            self.active_sessions,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            greeting_pool_ref=self.greeting_pool,
        )

    async def handle_ws_connection(self, websocket: websockets.ServerConnection):
        session = ClientSession(
            websocket=websocket,
            app_config=self.config,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            greeting_pool=self.greeting_pool,
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

    async def _prewarm_ai_greetings(self):
        conversation = self.config.conversation
        if not (
            conversation.enabled
            and conversation.greeting_enabled
            and conversation.greeting_ai_enabled
        ):
            return

        generator = getattr(self.llm_engine, "generate_greetings", None)
        if generator is None:
            return

        timeout = max(0.1, conversation.ai_control_timeout_ms / 1000.0)
        try:
            generated = await asyncio.wait_for(
                generator(conversation.greeting_pool_size),
                timeout=timeout,
            )
        except Exception as exc:
            logger.warning("Shared AI greeting prewarm failed: %s", exc)
            return

        greetings = []
        for item in generated or []:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in greetings:
                greetings.append(cleaned)
            if len(greetings) >= conversation.greeting_pool_size:
                break
        if not greetings:
            logger.warning("Shared AI greeting prewarm returned no usable phrases")
            return

        self.greeting_pool[:] = greetings
        logger.info("Shared AI greeting pool ready count=%d", len(self.greeting_pool))
        if conversation.audio_cache_enabled:
            await self.response_audio_cache.prewarm(
                self.greeting_pool,
                conversation.fixed_response_timeout_seconds,
            )

    async def start(self):
        # Warm the persistent LLM HTTP connection before accepting user turns.
        warmup = getattr(self.llm_engine, "warmup", None)
        if warmup is not None:
            await warmup()
        await self._prewarm_ai_greetings()

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

        # 1. Start HTTP & OTA server
        await self.http_server.start()

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
