import os
import time
import socket
import logging
import aiohttp
from aiohttp import web
from config.settings import AppConfig
from core.session import ClientSession

logger = logging.getLogger("HttpServer")

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class AiohttpWsAdapter:
    """
    Adapts aiohttp WebSocketResponse to match the websockets ServerConnection interface used by ClientSession.
    """
    def __init__(self, ws: web.WebSocketResponse, request: web.Request):
        self.ws = ws
        self.request = request

    async def send(self, data):
        if self.ws.closed:
            return False
        try:
            if isinstance(data, str):
                await self.ws.send_str(data)
            elif isinstance(data, bytes):
                await self.ws.send_bytes(data)
            else:
                return False
            return True
        except Exception as e:
            logger.debug(f"Aiohttp WS send error: {e}")
            return False

    async def close(self, code=1000, reason=""):
        if self.ws.closed:
            return
        await self.ws.close(code=code, message=str(reason or "").encode("utf-8"))

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            msg = await self.ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                return msg.data
            elif msg.type == aiohttp.WSMsgType.BINARY:
                return msg.data
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                raise StopAsyncIteration
            elif msg.type == aiohttp.WSMsgType.PING:
                await self.ws.pong()
            elif msg.type == aiohttp.WSMsgType.PONG:
                pass

class HttpServer:
    def __init__(
        self,
        app_config: AppConfig,
        active_sessions_ref,
        tts_engine=None,
        llm_engine=None,
        response_audio_cache=None,
        greeting_pool_ref=None,
    ):
        self.config = app_config
        self.active_sessions = active_sessions_ref
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        self.response_audio_cache = response_audio_cache
        self.greeting_pool = greeting_pool_ref if greeting_pool_ref is not None else []
        self.local_ip = get_local_ip()
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/ws", self.handle_ws)
        self.app.router.add_get("/ws/", self.handle_ws)
        self.app.router.add_post("/ota/", self.handle_ota)
        self.app.router.add_get("/ota/", self.handle_ota)
        self.app.router.add_post("/api/ota/", self.handle_ota)
        self.app.router.add_get("/api/ota/", self.handle_ota)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/diagnostics", self.handle_diagnostics)
        self.app.router.add_post("/api/test-voice", self.handle_test_voice)
        self.app.router.add_get("/api/prompt", self.handle_get_prompt)
        self.app.router.add_post("/api/prompt", self.handle_set_prompt)
        
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        if os.path.exists(static_dir):
            self.app.router.add_static("/static/", static_dir)

    async def handle_root(self, request: web.Request) -> web.StreamResponse:
        # If client requested WebSocket upgrade on root URL
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self.handle_ws(request)
        
        static_index = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
        if os.path.exists(static_index):
            with open(static_index, "r", encoding="utf-8") as f:
                return web.Response(
                    text=f.read(),
                    content_type="text/html",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
                )
        return web.Response(text="VeeTee Voice Assistant Backend is active.", content_type="text/plain")

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        
        adapter = AiohttpWsAdapter(ws, request)
        session = ClientSession(
            websocket=adapter,
            app_config=self.config,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            greeting_pool=self.greeting_pool,
        )
        await session.initialize()
        self.active_sessions[session.session_id] = session
        
        try:
            async for message in adapter:
                await session.handle_message(message)
        except Exception as e:
            logger.error(f"Error in HTTP WebSocket session: {e}")
        finally:
            await session.close()
            self.active_sessions.pop(session.session_id, None)
            
        return ws

    async def handle_ota(self, request: web.Request) -> web.Response:
        """
        Returns WebSocket server endpoint and time synchronization for ESP32 firmware.
        """
        host_header = request.headers.get("Host", "")
        proto_header = request.headers.get("X-Forwarded-Proto", "http")
        
        # If accessing via Tailscale Funnel / Public domain
        if "ts.net" in host_header or "https" in proto_header or request.scheme == "https":
            ws_url = f"wss://{host_header}/ws"
        else:
            host = self.local_ip
            ws_port = self.config.server.ws_port
            ws_url = f"ws://{host}:{ws_port}/"
        
        response_data = {
            "websocket": {
                "url": ws_url,
                "version": 1
            },
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": 420
            }
        }
        return web.json_response(response_data)

    async def handle_test_voice(self, request: web.Request) -> web.Response:
        if self.tts_engine is None:
            return web.json_response({"error": "TTS unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        text = str(payload.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "Text is required"}, status=400)
        if len(text) > 1000:
            return web.json_response({"error": "Text is too long"}, status=400)

        try:
            wav_bytes = await self.tts_engine.synthesize_wav(text)
        except Exception as e:
            logger.error(f"VieNeu test voice generation failed: {e}", exc_info=True)
            return web.json_response({"error": "TTS generation failed"}, status=500)

        return web.Response(
            body=wav_bytes,
            content_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    async def handle_get_prompt(self, request: web.Request) -> web.Response:
        if self.llm_engine is None or not hasattr(self.llm_engine, "get_base_prompt"):
            return web.json_response({"error": "LLM prompt unavailable"}, status=503)
        return web.json_response({"base_prompt": self.llm_engine.get_base_prompt()})

    async def handle_set_prompt(self, request: web.Request) -> web.Response:
        if self.llm_engine is None or not hasattr(self.llm_engine, "set_base_prompt"):
            return web.json_response({"error": "LLM prompt unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        base_prompt = str(payload.get("base_prompt", "")).strip()
        if not base_prompt:
            return web.json_response({"error": "Base prompt is required"}, status=400)
        if len(base_prompt) > 4000:
            return web.json_response({"error": "Base prompt is too long"}, status=400)

        try:
            self.llm_engine.set_base_prompt(base_prompt, persist=True)
        except (OSError, ValueError) as e:
            logger.error(f"Failed to update base prompt: {e}")
            return web.json_response({"error": "Could not save base prompt"}, status=500)

        # A persona change should take effect from the very next turn. Old
        # assistant replies can otherwise anchor the model to the previous
        # personality even though the system prompt has already changed.
        for session in self.active_sessions.values():
            dialogue = getattr(session, "dialogue", None)
            if dialogue is not None:
                dialogue.clear()
            session.processed_transcript = ""
            refresh_greetings = getattr(session, "refresh_ai_greetings", None)
            if refresh_greetings is not None:
                refresh_greetings()

        return web.json_response({"ok": True, "base_prompt": self.llm_engine.get_base_prompt()})

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self.active_sessions),
            "ip": self.local_ip,
            "asr": self.config.asr.provider,
            "llm": self.config.llm.model,
            "tts": self.config.tts.provider
        })

    async def handle_diagnostics(self, request: web.Request) -> web.Response:
        """Expose non-secret runtime capabilities used by the browser test console."""
        conversation = self.config.conversation
        return web.json_response({
            "server": {
                "barge_in_policy": self.config.server.barge_in_policy,
                "ws_port": self.config.server.ws_port,
                "http_port": self.config.server.http_port,
                "active_sessions": len(self.active_sessions),
            },
            "conversation": {
                "enabled": conversation.enabled,
                "wake_words": conversation.wake_words,
                "greeting_enabled": conversation.greeting_enabled,
                "greeting_text": conversation.greeting_text,
                "audio_cache_enabled": conversation.audio_cache_enabled,
                "idle_timeout_seconds": conversation.idle_timeout_seconds,
                "exit_commands": conversation.exit_commands,
                "goodbye_enabled": conversation.goodbye_enabled,
                "goodbye_text": conversation.goodbye_text,
                "wake_start_wait_ms": conversation.wake_start_wait_ms,
                "fixed_response_timeout_seconds": conversation.fixed_response_timeout_seconds,
                "close_grace_ms": conversation.close_grace_ms,
            },
            "protocol": {
                "versions": [1, 2, 3],
                "listening_modes": ["auto", "manual", "realtime"],
                "browser_input_format": "pcm16",
            },
        })

    async def start(self):
        host = self.config.server.host
        port = self.config.server.http_port
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"HTTP Server started at http://{self.local_ip}:{port}")
        logger.info(f"OTA Endpoint: http://{self.local_ip}:{port}/ota/")
