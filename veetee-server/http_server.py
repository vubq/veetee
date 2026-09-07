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
            return
        try:
            if isinstance(data, str):
                await self.ws.send_str(data)
            elif isinstance(data, bytes):
                await self.ws.send_bytes(data)
        except Exception as e:
            logger.debug(f"Aiohttp WS send error: {e}")

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
    def __init__(self, app_config: AppConfig, active_sessions_ref, tts_engine=None, llm_engine=None):
        self.config = app_config
        self.active_sessions = active_sessions_ref
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
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
        self.app.router.add_post("/api/test-voice", self.handle_test_voice)
        
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
            llm_engine=self.llm_engine
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

    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "active_sessions": len(self.active_sessions),
            "ip": self.local_ip,
            "asr": self.config.asr.provider,
            "llm": self.config.llm.model,
            "tts": self.config.tts.provider
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
