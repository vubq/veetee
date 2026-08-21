"""Local-only adapter between the pinned browser client and Veetee device contract."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from veetee_server.device_gateway.protocol import parse_and_validate_json

logger = logging.getLogger("veetee.digital_human_harness")


def _default_static_root() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "references/xiaozhi-esp32-server/main/digital-human"
    )


class HarnessSettings(BaseSettings):
    """Bounded local configuration for the compatibility harness."""

    model_config = SettingsConfigDict(env_prefix="VEETEE_HARNESS_", extra="ignore")

    server_http_url: str = "http://127.0.0.1:8080"
    server_websocket_url: str = "ws://127.0.0.1:8080/api/v1/devices/ws"
    static_root: Path = Field(default_factory=_default_static_root)
    listen_mode: Literal["auto", "manual", "realtime"] = "auto"
    aec: bool = False
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    message_max_bytes: int = Field(default=65536, ge=1024, le=1048576)
    id_max_length: int = Field(default=128, ge=1, le=256)
    ticket_ttl_seconds: float = Field(default=30.0, gt=0, le=300)


@dataclass(slots=True)
class _TicketStore:
    """Bounded one-time exchange so the real gateway token never enters a URL."""

    ttl_seconds: float
    max_items: int = 128
    _items: dict[str, tuple[str, float]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def issue(self, authorization: str) -> str:
        now = monotonic()
        async with self._lock:
            self._purge(now)
            while len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda key: self._items[key][1])
                self._items.pop(oldest)
            ticket = secrets.token_urlsafe(24)
            presented = f"Bearer {ticket}"
            self._items[presented] = (authorization, now + self.ttl_seconds)
            return ticket

    async def redeem(self, presented: str) -> str | None:
        now = monotonic()
        async with self._lock:
            self._purge(now)
            item = self._items.pop(presented, None)
            return item[0] if item is not None else None

    def _purge(self, now: float) -> None:
        for key, (_, expires_at) in tuple(self._items.items()):
            if expires_at <= now:
                self._items.pop(key, None)


def _query_value(websocket: WebSocket, name: str, max_length: int) -> str:
    value = websocket.query_params.get(name, "").strip()
    if not value or len(value) > max_length:
        raise ValueError(f"Missing or invalid {name}")
    return value


def translate_browser_text(
    raw: str,
    *,
    session_id: str | None,
    listen_mode: Literal["auto", "manual", "realtime"],
    aec: bool,
    max_bytes: int,
) -> dict[str, Any] | None:
    """Translates one browser control frame without forwarding browser-only fields."""
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("Browser JSON payload exceeds size limit")
    payload = parse_and_validate_json(raw)
    message_type = payload.get("type")
    if message_type == "hello":
        return {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "features": {"aec": aec},
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }
    if message_type == "listen":
        state = payload.get("state")
        if state == "detect":
            return None
        if state not in {"start", "stop"}:
            raise ValueError("Invalid browser listen state")
        translated: dict[str, Any] = {
            "type": "listen",
            "state": state,
            "session_id": session_id,
        }
        if state == "start":
            translated["mode"] = listen_mode
        return translated
    if message_type in {"abort", "goodbye", "pong"}:
        return {"type": message_type, "session_id": session_id}
    raise ValueError("Unsupported browser control frame")


async def _relay_browser_to_server(
    browser: WebSocket,
    server: Any,
    settings: HarnessSettings,
    session: dict[str, str | None],
) -> None:
    while True:
        event = await browser.receive()
        if event.get("type") == "websocket.disconnect":
            return
        data = event.get("bytes")
        if data is not None:
            if len(data) > settings.message_max_bytes:
                raise ValueError("Browser binary payload exceeds size limit")
            if data:
                await server.send(data)
            else:
                await server.send(
                    json.dumps(
                        {
                            "type": "listen",
                            "state": "stop",
                            "session_id": session["id"],
                        }
                    )
                )
            continue
        raw = event.get("text")
        if raw is None:
            raise ValueError("Unsupported browser WebSocket event")
        translated = translate_browser_text(
            raw,
            session_id=session["id"],
            listen_mode=settings.listen_mode,
            aec=settings.aec,
            max_bytes=settings.message_max_bytes,
        )
        if translated is not None:
            await server.send(json.dumps(translated))


async def _relay_server_to_browser(
    browser: WebSocket,
    server: Any,
    settings: HarnessSettings,
    session: dict[str, str | None],
) -> None:
    async for message in server:
        if isinstance(message, bytes):
            if len(message) > settings.message_max_bytes:
                raise ValueError("Server binary payload exceeds size limit")
            await browser.send_bytes(message)
            continue
        if len(message.encode("utf-8")) > settings.message_max_bytes:
            raise ValueError("Server JSON payload exceeds size limit")
        payload = parse_and_validate_json(message)
        if payload.get("type") == "hello":
            value = payload.get("session_id")
            if not isinstance(value, str) or not value:
                raise ValueError("Server hello omitted session_id")
            session["id"] = value
        if payload.get("type") == "ping":
            await server.send(json.dumps({"type": "pong", "session_id": session["id"]}))
            continue
        await browser.send_text(message)


def create_harness_app(settings: HarnessSettings | None = None) -> FastAPI:
    """Creates the local compatibility app without changing the product server."""
    cfg = settings or HarnessSettings()
    static_root = cfg.static_root.resolve()
    if not (static_root / "index.html").is_file():
        raise RuntimeError(f"Digital-human static root is invalid: {static_root}")

    app = FastAPI(title="Veetee digital-human compatibility harness")
    app.state.settings = cfg
    tickets = _TicketStore(ttl_seconds=cfg.ticket_ttl_seconds)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "veetee-digital-human-harness"}

    @app.get("/health")
    async def upstream_compatible_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/wakeword-ws")
    async def wakeword_websocket(websocket: WebSocket) -> None:
        """Keeps the optional upstream bridge connected without simulating detection."""
        await websocket.accept()
        await websocket.send_json({"type": "bridge_connected", "success": True})
        await websocket.send_json(
            {
                "type": "wakeword_config",
                "success": True,
                "payload": {"enabled": False, "wakeWords": []},
            }
        )
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > cfg.message_max_bytes:
                    await websocket.close(code=1009)
                    return
                payload = parse_and_validate_json(raw)
                request_id = payload.get("requestId")
                if request_id is not None:
                    await websocket.send_json(
                        {
                            "type": payload.get("type", ""),
                            "requestId": request_id,
                            "success": False,
                            "error": "Wake-word runtime is disabled in this harness",
                        }
                    )
        except WebSocketDisconnect:
            pass

    @app.api_route("/api/v1/devices/ota/check", methods=["GET", "POST"])
    async def ota_proxy(request: Request) -> JSONResponse:
        headers = {
            "Device-Id": request.headers.get("Device-Id", ""),
            "Client-Id": request.headers.get("Client-Id", ""),
            "Content-Type": request.headers.get("Content-Type", "application/json"),
        }
        body = await request.body()
        if len(body) > cfg.message_max_bytes:
            return JSONResponse(status_code=413, content={"code": "payload_too_large"})
        target = f"{cfg.server_http_url.rstrip('/')}/api/v1/devices/ota/check"
        try:
            async with httpx.AsyncClient(timeout=cfg.connect_timeout_seconds) as client:
                response = await client.request(
                    request.method, target, headers=headers, content=body
                )
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return JSONResponse(status_code=502, content={"code": "upstream_unavailable"})
        if response.status_code >= 400 or not isinstance(payload, dict):
            return JSONResponse(status_code=response.status_code, content=payload)
        websocket_config = payload.get("websocket")
        if not isinstance(websocket_config, dict):
            return JSONResponse(status_code=502, content={"code": "invalid_upstream_response"})
        token = websocket_config.get("token")
        if not isinstance(token, str) or not token:
            return JSONResponse(status_code=502, content={"code": "invalid_upstream_response"})
        authorization = token if token.startswith("Bearer ") else f"Bearer {token}"
        websocket_config["token"] = await tickets.issue(authorization)
        websocket_config["url"] = str(request.url_for("browser_websocket"))
        return JSONResponse(content=payload, headers={"Access-Control-Allow-Origin": "*"})

    @app.websocket("/ws", name="browser_websocket")
    async def browser_websocket(browser: WebSocket) -> None:
        await browser.accept()
        try:
            presented_ticket = _query_value(browser, "authorization", 256)
            device_id = _query_value(browser, "device-id", cfg.id_max_length)
            client_id = _query_value(browser, "client-id", cfg.id_max_length)
        except ValueError:
            await browser.close(code=1008)
            return
        authorization = await tickets.redeem(presented_ticket)
        if authorization is None:
            await browser.close(code=1008)
            return
        headers = {
            "Authorization": authorization,
            "Protocol-Version": "1",
            "Device-Id": device_id,
            "Client-Id": client_id,
        }
        session: dict[str, str | None] = {"id": None}
        try:
            async with connect(
                cfg.server_websocket_url,
                additional_headers=headers,
                max_size=cfg.message_max_bytes,
                open_timeout=cfg.connect_timeout_seconds,
                close_timeout=cfg.connect_timeout_seconds,
            ) as server:
                browser_task = asyncio.create_task(
                    _relay_browser_to_server(browser, server, cfg, session)
                )
                server_task = asyncio.create_task(
                    _relay_server_to_browser(browser, server, cfg, session)
                )
                done, pending = await asyncio.wait(
                    {browser_task, server_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    with suppress(asyncio.CancelledError):
                        await task
                for task in done:
                    task.result()
        except (ConnectionClosed, WebSocketDisconnect):
            pass
        except Exception as exc:
            logger.warning("compatibility_session_failed", extra={"error_type": type(exc).__name__})
            with suppress(Exception):
                await browser.close(code=1011)

    @app.get("/")
    async def index_redirect() -> RedirectResponse:
        return RedirectResponse("/index.html")

    app.mount("/", StaticFiles(directory=static_root, html=True), name="digital-human-static")
    return app


app = create_harness_app()
