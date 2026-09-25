import os
import time
import socket
import logging
import hmac
import json
from collections import deque
import aiohttp
from aiohttp import web
from config.settings import AppConfig
from core.session import ClientSession
from core.access import WebSocketAccess, serve_session
from core.assistant_runtime import build_assistant_llm_view, build_assistant_tts_view
from core.providers.tts.scheduler import TTSPreempted
from core.turn_metrics import TurnTraceStore, summarize_trace

logger = logging.getLogger("HttpServer")


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; "
        "style-src 'self'; font-src 'self' data:; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; worker-src 'self' blob:; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'",
    )
    return response


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
        recent_turn_store=None,
        runtime_readiness_ref=None,
        websocket_access=None,
        management_store=None,
        runtime_config_applier=None,
    ):
        self.config = app_config
        self.management_store = management_store
        self.runtime_config_applier = runtime_config_applier
        self.websocket_access = websocket_access or WebSocketAccess(
            app_config.server, app_config.management, management_store
        )
        self.active_sessions = active_sessions_ref
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        self.response_audio_cache = response_audio_cache
        self.recent_turn_store = recent_turn_store or TurnTraceStore(max_recent=100)
        self.runtime_readiness = runtime_readiness_ref if runtime_readiness_ref is not None else {}
        self._test_voice_active = 0
        self._test_voice_recent = deque()
        self.local_ip = get_local_ip()
        self.app = web.Application(
            client_max_size=64 * 1024,
            middlewares=[security_headers_middleware],
        )
        self._runner = None
        self._site = None
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/ws", self.handle_ws)
        self.app.router.add_get("/ws/", self.handle_ws)
        self.app.router.add_post("/api/session", self.handle_login)
        self.app.router.add_get("/api/assistants", self.handle_list_assistants)
        self.app.router.add_post("/api/assistants", self.handle_create_assistant)
        self.app.router.add_patch("/api/assistants/{assistant_id}", self.handle_update_assistant)
        self.app.router.add_delete("/api/assistants/{assistant_id}", self.handle_delete_assistant)
        self.app.router.add_get("/api/devices", self.handle_list_devices)
        self.app.router.add_get("/api/devices/pending", self.handle_list_pending_devices)
        self.app.router.add_post("/api/devices/pair", self.handle_pair_device)
        self.app.router.add_patch("/api/devices/{device_id}/{client_id}", self.handle_update_device)
        self.app.router.add_post("/api/devices/{device_id}/{client_id}/revoke", self.handle_revoke_device)
        self.app.router.add_get("/api/runtime-config", self.handle_get_runtime_config)
        self.app.router.add_patch("/api/runtime-config", self.handle_update_runtime_config)
        self.app.router.add_post("/ota/activate", self.handle_activate)
        self.app.router.add_post("/api/ota/activate", self.handle_activate)
        self.app.router.add_post("/ota/", self.handle_ota)
        self.app.router.add_get("/ota/", self.handle_ota)
        self.app.router.add_post("/api/ota/", self.handle_ota)
        self.app.router.add_get("/api/ota/", self.handle_ota)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/api/diagnostics", self.handle_diagnostics)
        self.app.router.add_get("/api/turns/{turn_id}", self.handle_turn_detail)
        self.app.router.add_post("/api/test-voice", self.handle_test_voice)
        self.app.router.add_get("/api/prompt", self.handle_get_prompt)
        self.app.router.add_post("/api/prompt", self.handle_set_prompt)
        self.app.router.add_get("/api/voice", self.handle_get_voice)
        self.app.router.add_post("/api/voice", self.handle_set_voice)
        self.app.router.add_get("/api/model", self.handle_get_model)
        self.app.router.add_post("/api/model", self.handle_set_model)
        
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

    async def handle_login(self, request):
        access = self.websocket_access
        if not access.origin_allowed(request.headers):
            return web.Response(status=403)
        authorization = str(request.headers.get("Authorization", "") or "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not access.manager_token_valid(supplied):
            return web.Response(status=401)
        response = web.json_response({"ok": True})
        response.set_cookie("veetee_session", access.manager_cookie(), max_age=3600,
                            httponly=True, samesite="Strict", secure=request.secure)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        access = self.websocket_access
        if not access.origin_allowed(request.headers):
            return web.Response(status=403)
        auth = access.authenticate(request.headers)
        if not auth:
            return web.Response(status=401)
        if access.active >= self.config.server.ws_max_sessions:
            return web.Response(status=503)
        ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=65536)
        await ws.prepare(request)
        
        adapter = AiohttpWsAdapter(ws, request)
        assistant = auth.get("assistant") if isinstance(auth, dict) else None
        auth_device = auth.get("device") if isinstance(auth, dict) else None
        owner_id = self.config.memory.trusted_owner_id
        if isinstance(auth_device, dict) and str(auth_device.get("owner_id") or "").strip():
            owner_id = str(auth_device["owner_id"]).strip()
        session_tts = build_assistant_tts_view(self.tts_engine, assistant)
        session_llm = build_assistant_llm_view(self.llm_engine, assistant, self.config)
        def factory():
            return ClientSession(
                websocket=adapter,
                app_config=self.config,
                tts_engine=session_tts,
                llm_engine=session_llm,
                response_audio_cache=self.response_audio_cache if assistant is None else None,
                recovery_audio_cache=self.response_audio_cache,
                turn_trace_store=self.recent_turn_store,
                authenticated_owner_id=owner_id,
            )
        try:
            await serve_session(adapter, access, self.config, self.active_sessions, factory)
        except Exception as e:
            logger.error(f"Error in HTTP WebSocket session: {e}")
            
        return ws

    def _ota_ws_url(self, request: web.Request) -> str:
        host_header = request.headers.get("Host", "")
        proto_header = request.headers.get("X-Forwarded-Proto", "")
        forwarded_proto = proto_header.split(",", 1)[0].strip().lower()
        host_for_match = host_header.strip().lower()
        if host_for_match.startswith("["):
            closing = host_for_match.find("]")
            host_name = host_for_match[1:closing] if closing > 0 else host_for_match
        else:
            host_name = host_for_match.split(":", 1)[0]
        host_name = host_name.rstrip(".")
        is_tailscale_funnel = host_name == "ts.net" or host_name.endswith(".ts.net")
        is_https = forwarded_proto == "https" or request.scheme == "https"
        if is_tailscale_funnel or is_https:
            return f"wss://{host_header}/ws"
        return f"ws://{self.local_ip}:{self.config.server.ws_port}/"

    def _device_headers(self, request: web.Request) -> tuple[str, str]:
        device_id = str(request.headers.get("Device-Id", "") or "").strip()
        client_id = str(request.headers.get("Client-Id", "") or "").strip()
        # Upstream treats Client-Id as optional and falls back to Device-Id.
        return device_id, client_id or device_id

    async def handle_ota(self, request: web.Request) -> web.Response:
        """Stock Xiaozhi OTA discovery + six-digit activation pairing."""
        device_id, client_id = self._device_headers(request)
        response_data = {
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": 420,
            }
        }
        if not self.management_store or not device_id or not client_id:
            return web.json_response(response_data, status=400)

        paired = self.management_store.get_device(device_id, client_id)
        if paired and not paired.get("revoked"):
            credential = self.management_store.claim_approved_credential(
                device_id,
                client_id,
                source_key=request.remote or "",
            )
            if credential:
                response_data["websocket"] = {
                    "url": self._ota_ws_url(request),
                    "version": 1,
                    "token": credential,
                }
            # Established stock firmware persists websocket settings locally.
            # Routine OTA checks therefore never re-disclose the bearer token.
            self.management_store.touch_device(device_id, client_id)
            return web.json_response(response_data, headers={"Cache-Control": "no-store"})

        metadata = {}
        try:
            if request.can_read_body:
                body = await request.json()
                if isinstance(body, dict):
                    metadata = {
                        "board": body.get("board") or body.get("chip_model") or "",
                        "app_version": body.get("application", {}).get("version", "")
                            if isinstance(body.get("application"), dict) else body.get("version", ""),
                    }
        except Exception:
            pass
        try:
            pending = self.management_store.ensure_pending(
                device_id,
                client_id,
                metadata,
                source_key=request.remote or "",
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            status = 429 if "rate limit" in str(exc) else 503
            return web.json_response(
                {"error": str(exc)},
                status=status,
                headers={"Cache-Control": "no-store"},
            )
        activation_version = str(request.headers.get("Activation-Version", "1") or "1").strip()
        response_data["activation"] = {
            "code": pending["code"],
            "message": "Nhập mã 6 số này trong VeeTee Manager để ghép thiết bị.",
            "timeout_ms": max(1000, int((pending["expires_at"] - time.time()) * 1000)),
        }
        # Version 1 firmware cannot prove a challenge (its activation body is
        # literally {}). Only advertise challenge mode to newer contracts.
        if activation_version != "1":
            response_data["activation"]["challenge"] = pending["challenge"]
        return web.json_response(response_data, headers={"Cache-Control": "no-store"})

    async def handle_activate(self, request: web.Request) -> web.Response:
        """Firmware polls this endpoint until the manager approves its code."""
        if not self.management_store:
            return web.json_response({"error": "pairing unavailable"}, status=503)
        device_id, client_id = self._device_headers(request)
        if not device_id or not client_id:
            return web.json_response({"error": "device identity required"}, status=400)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        challenge = str(payload.get("challenge", "") or "") if isinstance(payload, dict) else ""
        pending = self.management_store.pending_for_device(device_id, client_id)
        if not pending:
            return web.json_response({"status": "pending"}, status=202,
                                     headers={"Cache-Control": "no-store"})
        activation_version = str(request.headers.get("Activation-Version", "1") or "1").strip()
        if activation_version != "1" and challenge != str(pending.get("challenge") or ""):
            return web.json_response({"error": "activation challenge invalid"}, status=400)
        if not self.management_store.activation_approved(device_id, client_id, challenge):
            return web.json_response({"status": "pending"}, status=202,
                                     headers={"Cache-Control": "no-store"})
        return web.json_response({"status": "activated"}, headers={"Cache-Control": "no-store"})

    def _management_access_allowed(self, request: web.Request) -> bool:
        if self.websocket_access._manager_cookie_valid(request.headers):
            return True
        token = self.config.management.token.strip()
        if not token:
            return False
        supplied = request.headers.get("X-Veetee-Management-Token", "").strip()
        authorization = request.headers.get("Authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return bool(supplied) and hmac.compare_digest(supplied, token)

    def _management_denied(self) -> web.Response:
        return web.json_response(
            {"error": "Management access denied"},
            status=401,
            headers={"Cache-Control": "no-store"},
        )

    async def _json_body(self, request: web.Request) -> dict:
        try:
            payload = await request.json()
        except Exception as exc:
            raise ValueError("Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    async def handle_list_assistants(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        rows = self.management_store.list_assistants() if self.management_store else []
        devices = self.management_store.list_devices() if self.management_store else []
        counts = {}
        for device in devices:
            if device.get("revoked"):
                continue
            aid = str(device.get("assistant_id") or "")
            counts[aid] = counts.get(aid, 0) + 1
        for row in rows:
            row["device_count"] = counts.get(str(row.get("id")), 0)
            if not row.get("base_prompt"):
                row["base_prompt"] = self.config.llm.base_prompt
            if not row.get("voice"):
                row["voice"] = self._runtime_tts_voice()
            if not row.get("model"):
                row["model"] = self._runtime_llm_model()
        return web.json_response({"assistants": rows}, headers={"Cache-Control": "no-store"})

    async def handle_create_assistant(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if not self.management_store:
            return web.json_response({"error": "management store unavailable"}, status=503)
        try:
            payload = await self._json_body(request)
            from config.settings import validate_base_prompt_budget
            base_prompt = str(payload.get("base_prompt") or "").strip()
            if base_prompt:
                validate_base_prompt_budget(base_prompt, self.config)
            model = str(payload.get("model") or "").strip()
            if model and hasattr(self.llm_engine, "list_models") and model not in self.llm_engine.list_models():
                raise ValueError("model is not allowed")
            voice = str(payload.get("voice") or "").strip()
            if voice and hasattr(self.tts_engine, "available_voices"):
                names = {name for _, name in self.tts_engine.available_voices()}
                if names and voice not in names:
                    raise ValueError("voice is not available")
            row = self.management_store.create_assistant(
                name=str(payload.get("name") or ""),
                base_prompt=base_prompt,
                voice=voice,
                model=model,
            )
            return web.json_response({"assistant": row}, status=201)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_update_assistant(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if not self.management_store:
            return web.json_response({"error": "management store unavailable"}, status=503)
        try:
            payload = await self._json_body(request)
            if "base_prompt" in payload and str(payload.get("base_prompt") or "").strip():
                from config.settings import validate_base_prompt_budget
                validate_base_prompt_budget(str(payload["base_prompt"]), self.config)
            if "model" in payload and str(payload.get("model") or "").strip() and hasattr(self.llm_engine, "list_models"):
                if str(payload["model"]).strip() not in self.llm_engine.list_models():
                    raise ValueError("model is not allowed")
            if "voice" in payload and str(payload.get("voice") or "").strip() and hasattr(self.tts_engine, "available_voices"):
                names = {name for _, name in self.tts_engine.available_voices()}
                if names and str(payload["voice"]).strip() not in names:
                    raise ValueError("voice is not available")
            row = self.management_store.update_assistant(request.match_info["assistant_id"], payload)
            return web.json_response({"assistant": row})
        except KeyError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_delete_assistant(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        try:
            self.management_store.delete_assistant(request.match_info["assistant_id"])
            return web.json_response({"ok": True})
        except KeyError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=409)

    async def handle_list_devices(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        rows = self.management_store.list_devices() if self.management_store else []
        active_ids = {
            (str(getattr(s, "device_id", "")), str(getattr(s, "client_id", "")))
            for s in self.active_sessions.values()
        }
        for row in rows:
            row["online"] = (str(row.get("device_id", "")), str(row.get("client_id", ""))) in active_ids
        return web.json_response({"devices": rows}, headers={"Cache-Control": "no-store"})

    async def handle_list_pending_devices(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        rows = self.management_store.list_pending() if self.management_store else []
        return web.json_response({"pending": rows}, headers={"Cache-Control": "no-store"})

    async def handle_pair_device(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        try:
            payload = await self._json_body(request)
            row = self.management_store.pair_code(
                str(payload.get("code") or ""),
                str(payload.get("assistant_id") or ""),
                name=str(payload.get("name") or ""),
                owner_id=str(payload.get("owner_id") or self.config.memory.trusted_owner_id or ""),
            )
            return web.json_response({"device": row})
        except KeyError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_update_device(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        try:
            payload = await self._json_body(request)
            row = self.management_store.update_device(
                request.match_info["device_id"], request.match_info["client_id"],
                name=payload.get("name") if "name" in payload else None,
                assistant_id=payload.get("assistant_id") if "assistant_id" in payload else None,
                owner_id=payload.get("owner_id") if "owner_id" in payload else None,
                revoked=payload.get("revoked") if "revoked" in payload else None,
            )
            return web.json_response({"device": row})
        except KeyError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_revoke_device(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        try:
            row = self.management_store.update_device(
                request.match_info["device_id"], request.match_info["client_id"], revoked=True
            )
            for session in list(self.active_sessions.values()):
                if (str(getattr(session, "device_id", "")) == request.match_info["device_id"]
                        and str(getattr(session, "client_id", "")) == request.match_info["client_id"]):
                    await session.close()
            return web.json_response({"device": row})
        except KeyError as exc:
            return web.json_response({"error": str(exc)}, status=404)

    async def handle_get_runtime_config(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        values = self.management_store.runtime_public() if self.management_store else {}
        return web.json_response({
            "values": values,
            "restart_required": False,
            "note": "Secret values are masked. Supported runtime changes are applied immediately without restarting the service.",
        }, headers={"Cache-Control": "no-store"})

    async def handle_update_runtime_config(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        try:
            payload = await self._json_body(request)
            changes = payload.get("values", payload)
            if not isinstance(changes, dict):
                raise ValueError("runtime changes must be an object")

            if self.runtime_config_applier is not None:
                result = await self.runtime_config_applier(changes)
                if not isinstance(result, dict):
                    raise RuntimeError("runtime config applier returned invalid result")
                response = {
                    "values": result.get(
                        "values",
                        self.management_store.runtime_public() if self.management_store else {},
                    ),
                    "restart_required": False,
                    "applied": True,
                }
                for key in ("sessions_updated", "llm_reloaded", "llm_ready", "asr_reloaded"):
                    if key in result:
                        response[key] = result[key]
                return web.json_response(response, headers={"Cache-Control": "no-store"})

            # Compatibility path for standalone HttpServer use in tests/tools.
            # Production VeeTeeServer always supplies the hot-apply callback.
            values = self.management_store.update_runtime(changes)
            restart_required = False
            for key, value in changes.items():
                key = str(key)
                if key.startswith("GROQ_API_KEY_") or key == "HF_TOKEN":
                    if value is None or value == "":
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = str(value)
                    restart_required = True
                elif key in {"llm.model", "tts.voice"}:
                    if key == "llm.model" and value and hasattr(self.llm_engine, "set_model"):
                        self.llm_engine.set_model(str(value), persist=False)
                    if key == "tts.voice" and value and hasattr(self.tts_engine, "set_voice"):
                        self.tts_engine.set_voice(str(value), persist=False)
                elif key.startswith(ManagementStore.GROQ_TOKEN_LIMIT_PREFIX):
                    # Per-key daily budget is read dynamically by the
                    # router/store and never requires a process restart.
                    pass
                else:
                    restart_required = True
            return web.json_response({"values": values, "restart_required": restart_required})
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except RuntimeError as exc:
            logger.warning("Runtime hot-apply rejected: %s", exc)
            return web.json_response(
                {"error": f"Không thể áp dụng cấu hình ngay: {exc}"},
                status=400,
                headers={"Cache-Control": "no-store"},
            )

    def _admit_test_voice(self) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        while self._test_voice_recent and self._test_voice_recent[0] < cutoff:
            self._test_voice_recent.popleft()
        if len(self._test_voice_recent) >= self.config.management.test_voice_requests_per_minute:
            return False
        if self._test_voice_active >= self.config.management.test_voice_max_concurrency:
            return False
        self._test_voice_recent.append(now)
        self._test_voice_active += 1
        return True

    async def handle_test_voice(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
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

        if not self._admit_test_voice():
            return web.json_response({"error": "Test voice is busy or rate limited"}, status=429)

        try:
            wav_bytes = await self.tts_engine.synthesize_wav(text)
        except TTSPreempted:
            logger.info("Dashboard test voice yielded TTS engine to live conversation")
            return web.json_response(
                {
                    "error": "Test voice was interrupted by a live conversation",
                    "code": "TTS_PREEMPTED",
                },
                status=409,
            )
        except Exception as e:
            logger.error(f"VieNeu test voice generation failed: {e}", exc_info=True)
            return web.json_response({"error": "TTS generation failed"}, status=500)
        finally:
            self._test_voice_active = max(0, self._test_voice_active - 1)

        return web.Response(
            body=wav_bytes,
            content_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    async def handle_get_prompt(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.llm_engine is None or not hasattr(self.llm_engine, "get_base_prompt"):
            return web.json_response({"error": "LLM prompt unavailable"}, status=503)
        return web.json_response({"base_prompt": self.llm_engine.get_base_prompt()})

    async def handle_set_prompt(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.llm_engine is None or not hasattr(self.llm_engine, "set_base_prompt"):
            return web.json_response({"error": "LLM prompt unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        base_prompt = str(payload.get("base_prompt", "")).strip()
        if not base_prompt:
            return web.json_response({"error": "Base prompt is required"}, status=400)
        # Shared budget validation: bytes + estimated tokens, never silent truncate.
        try:
            from config.settings import validate_base_prompt_budget
            validate_base_prompt_budget(base_prompt, self.config)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        try:
            setter = getattr(self.llm_engine, "set_base_prompt", None)
            if setter is None:
                raise ValueError("LLM provider has no persona setter")
            try:
                setter(
                    base_prompt,
                    persist=True,
                    max_bytes=self.config.llm.base_prompt_max_bytes,
                    max_tokens_estimate=self.config.llm.base_prompt_max_tokens,
                    chars_per_token=self.config.latency.context_chars_per_token,
                )
            except TypeError:
                # Back-compat for test doubles without budget args.
                setter(base_prompt, persist=True)
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

        if self.response_audio_cache is not None:
            try:
                await self.response_audio_cache.clear_recovery()
                generator = getattr(self.llm_engine, "generate_recovery_message", None)
                if generator is None:
                    raise RuntimeError("LLM provider has no recovery-message generator")
                text = await generator()
                provenance = f"ai:{self.config.llm.model}"
                await self.response_audio_cache.prepare_recovery(
                    text,
                    self.config.conversation.fixed_response_timeout_seconds,
                    provenance=provenance,
                )
                self.runtime_readiness["error_fallback_ready"] = True
                self.runtime_readiness["error_fallback_provenance"] = provenance
            except Exception as exc:
                self.runtime_readiness["error_fallback_ready"] = False
                self.runtime_readiness["error_fallback_provenance"] = ""
                logger.warning("AI recovery asset refresh failed after persona change: %s", exc)

        return web.json_response({"ok": True, "base_prompt": self.llm_engine.get_base_prompt()})

    async def _refresh_recovery_audio(self, reason: str) -> None:
        """Regenerate the cached error-fallback clip (e.g. voice changed)."""
        if self.response_audio_cache is None:
            return
        try:
            await self.response_audio_cache.clear_recovery()
            generator = getattr(self.llm_engine, "generate_recovery_message", None)
            if generator is None:
                raise RuntimeError("LLM provider has no recovery-message generator")
            text = await generator()
            provenance = f"ai:{self._runtime_llm_model()}"
            await self.response_audio_cache.prepare_recovery(
                text,
                self.config.conversation.fixed_response_timeout_seconds,
                provenance=provenance,
            )
            self.runtime_readiness["error_fallback_ready"] = True
            self.runtime_readiness["error_fallback_provenance"] = provenance
        except Exception as exc:
            self.runtime_readiness["error_fallback_ready"] = False
            self.runtime_readiness["error_fallback_provenance"] = ""
            logger.warning("AI recovery asset refresh failed after %s: %s", reason, exc)

    def _runtime_llm_model(self) -> str:
        model = getattr(self.llm_engine, "model", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
        return self.config.llm.model

    def _runtime_tts_voice(self) -> str:
        voice = getattr(self.tts_engine, "voice", None)
        if isinstance(voice, str) and voice.strip():
            return voice.strip()
        return self.config.tts.voice

    async def handle_get_voice(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.tts_engine is None:
            return web.json_response({"error": "TTS unavailable"}, status=503)
        lister = getattr(self.tts_engine, "available_voices", None)
        voices = []
        if callable(lister):
            try:
                voices = [{"name": name, "description": desc}
                          for desc, name in lister()]
            except Exception as exc:
                logger.warning("Could not list TTS voices: %s", exc)
        return web.json_response({
            "voice": self._runtime_tts_voice(),
            "voices": voices,
        })

    async def handle_set_voice(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.tts_engine is None or not hasattr(self.tts_engine, "set_voice"):
            return web.json_response({"error": "TTS voice switching unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            voice = self.tts_engine.set_voice(str(payload.get("voice", "")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except OSError as exc:
            logger.error(f"Failed to persist voice: {exc}")
            return web.json_response({"error": "Could not save voice"}, status=500)
        # The cached fallback clip was synthesized in the old voice.
        await self._refresh_recovery_audio("voice change")
        return web.json_response({"ok": True, "voice": voice})

    async def handle_get_model(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.llm_engine is None:
            return web.json_response({"error": "LLM unavailable"}, status=503)
        lister = getattr(self.llm_engine, "list_models", None)
        models = list(lister()) if callable(lister) else [self._runtime_llm_model()]
        return web.json_response({
            "model": self._runtime_llm_model(),
            "models": models,
        })

    async def handle_set_model(self, request: web.Request) -> web.Response:
        if not self._management_access_allowed(request):
            return self._management_denied()
        if self.llm_engine is None or not hasattr(self.llm_engine, "set_model"):
            return web.json_response({"error": "LLM model switching unavailable"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            model = self.llm_engine.set_model(str(payload.get("model", "")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except OSError as exc:
            logger.error(f"Failed to persist model: {exc}")
            return web.json_response({"error": "Could not save model"}, status=500)
        return web.json_response({"ok": True, "model": model})

    async def _readiness_snapshot(self) -> dict:
        conversation = self.config.conversation
        fallback_ready = None
        fallback_provenance = ""
        if self.response_audio_cache is not None:
            try:
                recovery = await self.response_audio_cache.get_recovery()
                fallback_ready = True
                fallback_provenance = recovery.provenance
            except Exception:
                fallback_ready = False

        llm_ready = bool(self.runtime_readiness.get("llm_warm", self.llm_engine is not None))
        asr_ready = bool(self.runtime_readiness.get("asr_ready", True))
        tts_ready = self.tts_engine is not None
        error_fallback_ready = bool(
            fallback_ready
            or self.runtime_readiness.get("error_fallback_ready", False)
        )
        if fallback_ready:
            self.runtime_readiness["error_fallback_ready"] = True
            self.runtime_readiness["error_fallback_provenance"] = fallback_provenance

        reasons = []
        if not llm_ready:
            reasons.append("llm_not_warm")
        if not asr_ready:
            reasons.append("asr_not_ready")
        if not tts_ready:
            reasons.append("tts_unavailable")
        # Recovery audio is an optional AI-authored capability, not a critical
        # readiness dependency. Report it below for observability without
        # marking an otherwise usable ASR/LLM/TTS server as degraded.
        return {
            "status": "ready" if not reasons else "degraded",
            "degraded_reasons": reasons,
            "llm": llm_ready,
            "asr": asr_ready,
            "tts": tts_ready,
            "error_fallback_audio": error_fallback_ready,
            "error_fallback_provenance": self.runtime_readiness.get(
                "error_fallback_provenance",
                fallback_provenance,
            ),
            "greeting": {
                "mode": "semantic_turn",
                "ready": llm_ready,
            },
        }

    async def handle_health(self, request: web.Request) -> web.Response:
        readiness = await self._readiness_snapshot()
        return web.json_response({
            "status": "healthy" if readiness["status"] == "ready" else "degraded",
            "liveness": "alive",
            "readiness": readiness["status"],
            "degraded_reasons": readiness["degraded_reasons"],
            "active_sessions": len(self.active_sessions),
            "ip": self.local_ip,
            "asr": self.config.asr.provider,
            "llm": self._runtime_llm_model(),
            "tts": self.config.tts.provider,
            "voice": self._runtime_tts_voice()
        })

    async def handle_turn_detail(self, request: web.Request) -> web.Response:
        """Return one retained turn trace for authenticated review/debugging."""
        if not self._management_access_allowed(request):
            return self._management_denied()

        turn_id = str(request.match_info.get("turn_id") or "").strip()
        if not turn_id or len(turn_id) > 256:
            return web.json_response({"error": "Invalid turn id"}, status=400)

        for trace in reversed(self.recent_turn_store.recent):
            if str(getattr(trace, "turn_id", "") or "") != turn_id:
                continue
            payload = summarize_trace(trace)
            # The detail endpoint intentionally exposes event-level operational
            # evidence to authenticated management clients. Scrub obvious
            # credential-shaped fields defensively; semantic text/tool receipts
            # remain visible for human review.
            def scrub(value):
                if isinstance(value, dict):
                    output = {}
                    for key, item in value.items():
                        lowered = str(key or "").strip().lower()
                        if lowered in {"api_key", "token", "password", "secret"} or lowered.endswith(
                            ("_api_key", "_token", "_password", "_secret")
                        ):
                            output[key] = "<redacted>"
                        else:
                            output[key] = scrub(item)
                    return output
                if isinstance(value, list):
                    return [scrub(item) for item in value]
                return value

            return web.json_response(scrub(payload))

        return web.json_response({"error": "Turn not found"}, status=404)

    async def handle_diagnostics(self, request: web.Request) -> web.Response:
        """Expose runtime diagnostics to authenticated management clients."""
        if not self._management_access_allowed(request):
            return self._management_denied()
        conversation = self.config.conversation
        session_rows = []
        retained_traces = list(self.recent_turn_store.recent)
        recent_turns = [summarize_trace(trace) for trace in retained_traces]
        total_rounds = 0
        total_tool_calls = 0
        for trace in retained_traces:
            finish = trace.first("turn_finish")
            if finish is not None:
                total_rounds += int(finish.fields.get("llm_rounds") or 0)
                total_tool_calls += int(finish.fields.get("tool_calls") or 0)
        for session_id, session in self.active_sessions.items():
            recorder = getattr(session, "turn_metrics", None)
            traces = list(getattr(recorder, "recent", []) or [])

            mcp = getattr(session, "mcp_device", None)
            executor = getattr(session, "tool_executor", None)
            playback = getattr(session, "playback", None)
            context_builder = getattr(session, "context_builder", None)
            session_rows.append({
                "session_id": session_id,
                "state": str(getattr(getattr(session, "state", None), "value", getattr(session, "state", "unknown"))),
                "turns_recorded": len(traces),
                "mcp": mcp.snapshot() if mcp is not None and hasattr(mcp, "snapshot") else {
                    "enabled": False,
                    "ready": False,
                },
                "tools": executor.snapshot() if executor is not None and hasattr(executor, "snapshot") else {
                    "active_count": 0,
                    "receipt_count": 0,
                },
                "playback": playback.snapshot() if playback is not None and hasattr(playback, "snapshot") else {
                    "active": False,
                    "owner": "",
                    "generation": None,
                },
                "context_lookup": dict(getattr(context_builder, "last_lookup", {}) or {}),
            })

        tts_scheduler = None
        if self.tts_engine is not None and hasattr(self.tts_engine, "scheduler_snapshot"):
            try:
                tts_scheduler = self.tts_engine.scheduler_snapshot()
            except Exception:
                tts_scheduler = {"available": False}

        audio_cache = None
        if self.response_audio_cache is not None and hasattr(self.response_audio_cache, "snapshot"):
            audio_cache = self.response_audio_cache.snapshot()
        readiness = await self._readiness_snapshot()
        health_fn = getattr(self.llm_engine, "health", None)
        try:
            llm_health = health_fn() if callable(health_fn) else {
                "available": bool(readiness.get("llm")),
                "provider": self.config.llm.provider,
                "model": self._runtime_llm_model(),
            }
        except Exception as exc:
            llm_health = {
                "available": False,
                "provider": self.config.llm.provider,
                "model": self._runtime_llm_model(),
                "reason": f"health_snapshot_failed:{type(exc).__name__}",
            }

        quota_fn = getattr(self.llm_engine, "quota_snapshot", None)
        if callable(quota_fn):
            try:
                llm_health["quota"] = await quota_fn()
            except Exception as exc:
                llm_health["quota_error"] = (
                    f"quota_snapshot_failed:{type(exc).__name__}"
                )

        memory = self.config.memory
        tools = self.config.tools
        latency = self.config.latency
        intent = self.config.intent
        return web.json_response({
            "llm": llm_health,
            "asr": {
                "provider": self.config.asr.provider,
                "device": self.config.asr.device,
                "min_silence_duration_ms": self.config.asr.min_silence_duration_ms,
                "min_speech_duration_ms": self.config.asr.min_speech_duration_ms,
                "speech_start_frames": self.config.asr.speech_start_frames,
                "pre_speech_pad_ms": self.config.asr.pre_speech_pad_ms,
                "vad_threshold": self.config.asr.vad_threshold,
                "vad_threshold_low": self.config.asr.vad_threshold_low,
                "vad_end_threshold": self.config.asr.vad_end_threshold,
            },
            "tts": {
                "provider": self.config.tts.provider,
                "voice": self._runtime_tts_voice(),
                "send_ahead_ms": self.config.tts.send_ahead_ms,
                "stream_queue_max_chunks": self.config.tts.stream_queue_max_chunks,
                "first_audio_priority_boost": self.config.tts.first_audio_priority_boost,
                "scheduler_aging_per_second": self.config.tts.scheduler_aging_per_second,
                "admission_timeout_ms": self.config.tts.admission_timeout_ms,
                "speculative_prefetch_enabled": self.config.tts.speculative_prefetch_enabled,
                "frame_duration_ms": self.config.tts.frame_duration_ms,
            },
            "server": {
                "barge_in_policy": self.config.server.barge_in_policy,
                "ws_port": self.config.server.ws_port,
                "http_port": self.config.server.http_port,
                "active_sessions": len(self.active_sessions),
            },
            "conversation": {
                "enabled": conversation.enabled,
                "semantic_routing": "ai",
                "audio_cache_enabled": conversation.audio_cache_enabled,
                "idle_timeout_seconds": conversation.idle_timeout_seconds,
                "history_turns": conversation.history_turns,
                "wake_start_wait_ms": conversation.wake_start_wait_ms,
                "fixed_response_timeout_seconds": conversation.fixed_response_timeout_seconds,
                "close_grace_ms": conversation.close_grace_ms,
            },
            "protocol": {
                "versions": [1, 2, 3],
                "listening_modes": ["auto", "manual", "realtime"],
                "browser_input_format": "pcm16",
            },
            "profile": {
                "asr": {
                    "provider": self.config.asr.provider,
                    "device": self.config.asr.device,
                        "min_silence_duration_ms": self.config.asr.min_silence_duration_ms,
                    "speculative_inference_enabled": self.config.asr.speculative_inference_enabled,
                    "speculative_start_silence_ms": self.config.asr.speculative_start_silence_ms,
                    "speculative_min_confidence": self.config.asr.speculative_min_confidence,
                    "speculative_llm_enabled": self.config.asr.speculative_llm_enabled,
                    "speculative_llm_min_confidence": self.config.asr.speculative_llm_min_confidence,
                    "min_speech_duration_ms": self.config.asr.min_speech_duration_ms,
                    "speech_start_frames": self.config.asr.speech_start_frames,
                    "pre_speech_pad_ms": self.config.asr.pre_speech_pad_ms,
                    "vad_threshold": self.config.asr.vad_threshold,
                    "vad_threshold_low": self.config.asr.vad_threshold_low,
                    "vad_end_threshold": self.config.asr.vad_end_threshold,
                },
                "tts": {
                    "provider": self.config.tts.provider,
                    "voice": self._runtime_tts_voice(),
                    "send_ahead_ms": self.config.tts.send_ahead_ms,
                    "stream_queue_max_chunks": self.config.tts.stream_queue_max_chunks,
                    "first_audio_priority_boost": self.config.tts.first_audio_priority_boost,
                    "scheduler_aging_per_second": self.config.tts.scheduler_aging_per_second,
                    "admission_timeout_ms": self.config.tts.admission_timeout_ms,
                    "speculative_prefetch_enabled": self.config.tts.speculative_prefetch_enabled,
                    "frame_duration_ms": self.config.tts.frame_duration_ms,
                },
                "latency": {
                    "target_first_audio_ms": latency.target_first_audio_ms,
                    "first_token_timeout_ms": latency.first_token_timeout_ms,
                    "total_turn_timeout_ms": latency.total_turn_timeout_ms,
                },
                "routing": {
                    "headroom_pct": self.config.llm.routing.headroom_pct,
                    "max_attempts": self.config.llm.routing.max_attempts,
                    "admission_wait_ms": self.config.llm.routing.admission_wait_ms,
                    "discovery_wait_ms": self.config.llm.routing.discovery_wait_ms,
                    "discovery_max_inflight": self.config.llm.routing.discovery_max_inflight,
                    "inflight_penalty_s": self.config.llm.routing.inflight_penalty_s,
                    "latency_ewma_alpha": self.config.llm.routing.latency_ewma_alpha,
                    "latency_jitter_penalty": self.config.llm.routing.latency_jitter_penalty,
                },
                "speech_segmentation": {
                    "min_segment_chars": self.config.llm.speech_segmentation.min_segment_chars,
                    "clause_target_chars": self.config.llm.speech_segmentation.clause_target_chars,
                    "clause_min_chars": self.config.llm.speech_segmentation.clause_min_chars,
                    "first_clause_min_chars": self.config.llm.speech_segmentation.first_clause_min_chars,
                    "first_clause_min_words": self.config.llm.speech_segmentation.first_clause_min_words,
                    "hard_max_segment_chars": self.config.llm.speech_segmentation.hard_max_segment_chars,
                    "hard_cut_search_back": self.config.llm.speech_segmentation.hard_cut_search_back,
                    "hard_cut_search_forward": self.config.llm.speech_segmentation.hard_cut_search_forward,
                    "first_segment_min_chars": self.config.llm.speech_segmentation.first_segment_min_chars,
                    "first_segment_min_words": self.config.llm.speech_segmentation.first_segment_min_words,
                    "first_soft_cut_chars": self.config.llm.speech_segmentation.first_soft_cut_chars,
                    "first_soft_cut_min_words": self.config.llm.speech_segmentation.first_soft_cut_min_words,
                },
                "intent": {
                    "enabled": intent.enabled,
                    "semantic_end_enabled": intent.semantic_end_enabled,
                },
                "memory": {
                    "enabled": memory.enabled,
                    "durable_enabled": memory.durable_enabled,
                    "trusted_owner_bound": bool(memory.trusted_owner_id.strip()),
                    "lookup_timeout_ms": memory.lookup_timeout_ms,
                    "top_k": memory.top_k,
                    "max_memory_chars": memory.max_memory_chars,
                },
                "tools": {
                    "enabled": tools.enabled,
                    "native_enabled": tools.native_enabled,
                    "mcp_device_enabled": tools.mcp_device_enabled,
                    "max_calls_per_turn": tools.max_calls_per_turn,
                    "schema_limit": tools.schema_limit,
                    "max_llm_rounds_per_turn": tools.max_llm_rounds_per_turn,
                    "max_parallel_read_only": tools.max_parallel_read_only,
                    "tool_result_synthesis": tools.tool_result_synthesis,
                },
            },
            "readiness": {
                **readiness,
                "audio_cache": audio_cache,
                "tts_scheduler": tts_scheduler,
            },
            "runtime": {
                "recent_turn_count": len(retained_traces),
                "recent_turn_capacity": self.recent_turn_store.max_recent,
                "llm_round_count": total_rounds,
                "tool_call_count": total_tool_calls,
                "latest_turns": [
                    {
                        "session_id": item.get("session_id"),
                        "turn_id": item.get("turn_id"),
                        "capture_id": item.get("capture_id"),
                        "source": item.get("source"),
                        "outcome": item.get("outcome"),
                        "llm_rounds": item.get("llm_rounds"),
                        "tool_calls": item.get("tool_calls"),
                        "latency_ms": item.get("latency_ms", {}),
                        "stage_aggregates_ms": item.get("stage_aggregates_ms", {}),
                        "turn_start_to_first_ws_binary_ms": item.get("turn_start_to_first_ws_binary_ms"),
                    }
                    for item in recent_turns[-50:]
                ],
                "sessions": session_rows,
            },
        })

    async def start(self):
        if self._runner is not None:
            return
        host = self.config.server.host
        port = self.config.server.http_port
        runner = web.AppRunner(self.app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host, port)
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        logger.info(f"HTTP Server started at http://{self.local_ip}:{port}")
        logger.info(f"OTA Endpoint: http://{self.local_ip}:{port}/ota/")

    async def stop(self):
        runner, self._runner = self._runner, None
        self._site = None
        if runner is not None:
            await runner.cleanup()
