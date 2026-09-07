import asyncio
import os
import sys
import signal
import logging
import websockets

from config.settings import load_settings, AppConfig
from core.providers.tts.vieneu_local import VieneuLocalTTS
from core.providers.llm.omniroute_groq import OmnirouteGroqLLM
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
        logger.info(f"Loaded config: LLM provider=OmniRoute, model={config.llm.model}, max_tokens={config.llm.max_tokens}")
        
        # 1. Initialize Vieneu Neural TTS
        self.tts_engine = VieneuLocalTTS(
            voice=config.tts.voice,
            sample_rate=config.tts.sample_rate,
            frame_duration_ms=config.tts.frame_duration_ms,
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
            system_prompt=config.llm.system_prompt
        )
        
        self.active_sessions = {}
        self.http_server = HttpServer(
            self.config,
            self.active_sessions,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine
        )

    async def handle_ws_connection(self, websocket: websockets.ServerConnection):
        session = ClientSession(
            websocket=websocket,
            app_config=self.config,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine
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

    async def start(self):
        # Warm the persistent LLM HTTP connection before accepting user turns.
        warmup = getattr(self.llm_engine, "warmup", None)
        if warmup is not None:
            await warmup()

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
            close_llm = getattr(self.llm_engine, "close", None)
            if close_llm is not None:
                await close_llm()

async def main():
    config = load_settings()
    server = VeeTeeServer(config)
    await server.start()

if __name__ == "__main__":
    asyncio.run(main())
