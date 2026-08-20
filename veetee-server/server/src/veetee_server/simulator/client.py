"""Veetee Device Simulator implementation."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import websockets

from veetee_server.audio import (
    AudioPacketMetadata,
    encode_audio_frame,
    parse_audio_frame,
)
from veetee_server.simulator.transport import (
    RealWebSocketTransport,
    SimulatorTransportError,
    WebSocketTransport,
)


def find_contracts_dir(start_path: Path | None = None) -> Path:
    """Finds contracts/device directory by climbing up from start_path or cwd."""
    current = (start_path or Path(__file__)).resolve()
    search_paths = [current] + list(current.parents)
    for path in search_paths:
        candidate = path / "contracts" / "device"
        if candidate.is_dir():
            return candidate
        candidate2 = path / "veetee-server" / "contracts" / "device"
        if candidate2.is_dir():
            return candidate2
    raise FileNotFoundError("Could not locate contracts/device directory")


def load_golden_contract(filename: str, contracts_dir: Path | None = None) -> dict[str, Any]:
    """Loads and parses a golden contract fixture JSON file."""
    if Path(filename).name != filename or not filename.endswith(".json"):
        raise ValueError("Golden contract filename must be a local JSON basename")
    c_dir = contracts_dir or find_contracts_dir()
    file_path = c_dir / filename
    if not file_path.is_file():
        raise FileNotFoundError(f"Golden vector contract file not found: {file_path}")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Golden contract {filename} root must be a JSON object")
    return data


@dataclass
class SimulatorConfig:
    """Configuration options for VeeteeDeviceSimulator."""

    server_url: str = "http://127.0.0.1:8080"
    device_id: str = "sim-device-001"
    client_id: str = "sim-client-001"
    token: str = ""
    protocol_version: Literal[1, 2, 3] = 1
    user_agent: str = "VeeteeDeviceSimulator/1.0"
    accept_language: str = "vi-VN"
    extra_headers: dict[str, str] = field(default_factory=dict)


class VeeteeDeviceSimulator:
    """Device simulator for testing Veetee OTA discovery and WebSocket binary protocol."""

    def __init__(self, config: SimulatorConfig | None = None) -> None:
        self.config = config or SimulatorConfig()
        self.transport: WebSocketTransport | None = None
        self.session_id: str | None = None
        self.downlink_audio_params: dict[str, Any] | None = None

    def _get_http_ota_url(self) -> str:
        base = self.config.server_url.rstrip("/")
        if base.startswith("ws://"):
            base = "http://" + base[5:]
        elif base.startswith("wss://"):
            base = "https://" + base[6:]
        if not base.endswith("/api/v1/devices/ota/check"):
            return f"{base}/api/v1/devices/ota/check"
        return base

    def _get_ws_gateway_url(self) -> str:
        base = self.config.server_url.rstrip("/")
        if base.startswith("http://"):
            base = "ws://" + base[7:]
        elif base.startswith("https://"):
            base = "wss://" + base[8:]
        if not base.endswith("/api/v1/devices/ws"):
            return f"{base}/api/v1/devices/ws"
        return base

    async def ota_check(
        self,
        payload: dict[str, Any] | None = None,
        method: Literal["GET", "POST"] = "POST",
    ) -> dict[str, Any]:
        """Executes OTA discovery check against the server OTA endpoint."""
        url = self._get_http_ota_url()
        headers = {
            "Device-Id": self.config.device_id,
            "Client-Id": self.config.client_id,
            "User-Agent": self.config.user_agent,
            "Accept-Language": self.config.accept_language,
            **self.config.extra_headers,
        }

        body = payload
        if method == "POST" and body is None:
            body = load_golden_contract("ota_check_request.json")

        async with httpx.AsyncClient() as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.post(url, headers=headers, json=body)

        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("OTA response must be a JSON object")

        # Auto-configure token/url if server returned them
        if "websocket" in data and isinstance(data["websocket"], dict):
            ws_info = data["websocket"]
            if "token" in ws_info and isinstance(ws_info["token"], str):
                self.config.token = ws_info["token"]
            if "url" in ws_info and isinstance(ws_info["url"], str):
                self.config.server_url = ws_info["url"]

        return data

    async def connect_ws(
        self, custom_transport: WebSocketTransport | None = None
    ) -> WebSocketTransport:
        """Establishes WebSocket connection or attaches provided custom transport."""
        if custom_transport is not None:
            self.transport = custom_transport
            return custom_transport

        ws_url = self._get_ws_gateway_url()
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Protocol-Version": str(self.config.protocol_version),
            "Device-Id": self.config.device_id,
            "Client-Id": self.config.client_id,
            **self.config.extra_headers,
        }

        ws_conn = await websockets.connect(ws_url, additional_headers=headers)
        self.transport = RealWebSocketTransport(ws_conn)
        return self.transport

    def _require_transport(self) -> WebSocketTransport:
        if self.transport is None:
            raise SimulatorTransportError("WebSocket is not connected. Call connect_ws() first.")
        return self.transport

    async def send_hello(
        self,
        features: dict[str, bool] | None = None,
        audio_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends protocol hello message and parses hello response from server."""
        transport = self._require_transport()
        hello_payload = {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "features": features if features is not None else {"aec": True, "mcp": True},
            "audio_params": audio_params
            or {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        }

        await transport.send_text(json.dumps(hello_payload))
        resp = await self.receive_json()
        if resp.get("type") != "hello":
            raise SimulatorTransportError(f"Expected hello response, got {resp}")

        session_id = resp.get("session_id")
        if isinstance(session_id, str):
            self.session_id = session_id
        if "audio_params" in resp and isinstance(resp["audio_params"], dict):
            self.downlink_audio_params = resp["audio_params"]

        return resp

    async def send_listen(
        self, state: str = "start", mode: str | None = "auto"
    ) -> dict[str, Any] | None:
        """Sends listen control message to server."""
        transport = self._require_transport()
        payload: dict[str, Any] = {
            "type": "listen",
            "state": state,
            "session_id": self.session_id,
        }
        if mode is not None:
            payload["mode"] = mode
        await transport.send_text(json.dumps(payload))
        return None

    async def send_audio_frame(self, frame_bytes: bytes) -> None:
        """Sends a raw binary frame directly."""
        transport = self._require_transport()
        await transport.send_bytes(frame_bytes)

    async def send_audio_packet(self, packet: AudioPacketMetadata) -> None:
        """Encodes an AudioPacketMetadata packet and sends as binary frame."""
        frame_bytes = encode_audio_frame(packet)
        await self.send_audio_frame(frame_bytes)

    async def run_turn(
        self, frame_bytes: bytes, frame_count: int = 2
    ) -> list[dict[str, Any] | AudioPacketMetadata]:
        """Runs one complete fake-AI turn and validates the event sequence."""
        if frame_count <= 0:
            raise ValueError("frame_count must be positive")
        await self.send_listen(state="start", mode="auto")
        for _ in range(frame_count):
            await self.send_audio_frame(frame_bytes)
        await self.send_listen(state="stop", mode=None)

        events: list[dict[str, Any] | AudioPacketMetadata] = []
        stt = await self.receive_json()
        if stt.get("type") != "stt" or not stt.get("text"):
            raise SimulatorTransportError(f"Expected non-empty STT event, got {stt}")
        events.append(stt)

        start = await self.receive_json()
        if start.get("type") != "tts" or start.get("state") != "start":
            raise SimulatorTransportError(f"Expected TTS start event, got {start}")
        events.append(start)

        sentence = await self.receive_json()
        if sentence.get("type") != "tts" or sentence.get("state") != "sentence_start":
            raise SimulatorTransportError(f"Expected TTS sentence_start event, got {sentence}")
        events.append(sentence)

        while True:
            event = await self.receive_event()
            if isinstance(event, AudioPacketMetadata):
                events.append(event)
                continue
            if isinstance(event, bytes):
                raise SimulatorTransportError(f"Unexpected raw binary event: {event!r}")
            if event.get("type") == "tts" and event.get("state") == "stop":
                events.append(event)
                break
            raise SimulatorTransportError(f"Unexpected pipeline event: {event}")
        if not any(isinstance(event, AudioPacketMetadata) for event in events):
            raise SimulatorTransportError("Pipeline returned no audio packets")
        return events

    async def send_ping(self) -> None:
        """Sends ping control frame."""
        transport = self._require_transport()
        payload = {"type": "ping", "session_id": self.session_id}
        await transport.send_text(json.dumps(payload))

    async def send_pong(self) -> None:
        """Sends pong control frame."""
        transport = self._require_transport()
        payload = {"type": "pong", "session_id": self.session_id}
        await transport.send_text(json.dumps(payload))

    async def send_abort(self) -> None:
        """Sends abort control frame."""
        transport = self._require_transport()
        payload = {"type": "abort", "session_id": self.session_id}
        await transport.send_text(json.dumps(payload))

    async def send_goodbye(self) -> dict[str, Any]:
        """Sends goodbye control frame and waits for server goodbye response."""
        transport = self._require_transport()
        payload = {"type": "goodbye", "session_id": self.session_id}
        await transport.send_text(json.dumps(payload))
        resp = await self.receive_json()
        if resp.get("type") != "goodbye":
            raise SimulatorTransportError(f"Expected goodbye response, got {resp}")
        return resp

    async def receive_event(
        self, timeout: float = 5.0, auto_respond_ping: bool = True
    ) -> dict[str, Any] | AudioPacketMetadata | bytes:
        """Receives next WebSocket event (JSON text or binary audio frame)."""
        transport = self._require_transport()
        while True:
            msg = await transport.receive_msg(timeout=timeout)
            if isinstance(msg, str):
                try:
                    parsed = json.loads(msg)
                    if isinstance(parsed, dict):
                        if auto_respond_ping and parsed.get("type") == "ping":
                            await self.send_pong()
                            continue
                        return parsed
                    return {"type": "raw_text", "content": msg}
                except json.JSONDecodeError:
                    return {"type": "raw_text", "content": msg}

            # Handle binary audio frame
            try:
                return parse_audio_frame(
                    msg,
                    negotiated_version=self.config.protocol_version,
                )
            except Exception:
                return msg

    async def receive_json(self, timeout: float = 5.0) -> dict[str, Any]:
        """Receives a JSON object event from the server."""
        event = await self.receive_event(timeout=timeout)
        if isinstance(event, dict):
            return event
        raise SimulatorTransportError(f"Expected JSON event, got {type(event)}: {event!r}")

    async def receive_audio(self, timeout: float = 5.0) -> AudioPacketMetadata:
        """Receives a binary audio packet event from the server."""
        event = await self.receive_event(timeout=timeout)
        if isinstance(event, AudioPacketMetadata):
            return event
        raise SimulatorTransportError(
            f"Expected AudioPacketMetadata event, got {type(event)}: {event!r}"
        )

    async def close(self, code: int = 1000) -> None:
        """Closes the active WebSocket transport if connected."""
        if self.transport is not None:
            await self.transport.close(code=code)
            self.transport = None
