"""WebSocket transport abstractions for Veetee Device Simulator."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from websockets.asyncio.client import ClientConnection


class SimulatorTransportError(Exception):
    """Base exception for transport errors in device simulator."""


class WebSocketTransport(ABC):
    """Abstract interface for WebSocket connections used by VeeteeDeviceSimulator."""

    @abstractmethod
    async def send_text(self, data: str) -> None:
        """Sends a text frame over the WebSocket."""

    @abstractmethod
    async def send_bytes(self, data: bytes) -> None:
        """Sends a binary frame over the WebSocket."""

    @abstractmethod
    async def receive_msg(self, timeout: float = 5.0) -> str | bytes:
        """Receives next message (text or bytes) within specified timeout."""

    @abstractmethod
    async def close(self, code: int = 1000) -> None:
        """Closes the WebSocket connection."""


class RealWebSocketTransport(WebSocketTransport):
    """Production/CLI transport wrapper using websockets.asyncio.client."""

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws

    async def send_text(self, data: str) -> None:
        await self._ws.send(data)

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send(data)

    async def receive_msg(self, timeout: float = 5.0) -> str | bytes:
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
            if isinstance(msg, (str, bytes)):
                return msg
            raise SimulatorTransportError(f"Unexpected frame message type: {type(msg)}")
        except TimeoutError as exc:
            raise SimulatorTransportError(f"Receive timeout after {timeout} seconds") from exc

    async def close(self, code: int = 1000) -> None:
        await self._ws.close(code=code)


class TestClientWebSocketTransport(WebSocketTransport):
    """Test transport wrapper using Starlette/FastAPI TestClient's WebSocketTestSession."""

    __test__ = False

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send_text(self, data: str) -> None:
        self._ws.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        self._ws.send_bytes(data)

    async def receive_msg(self, timeout: float = 5.0) -> str | bytes:
        del timeout  # The in-process server owns timeout enforcement in contract tests.
        raw = self._ws.receive()
        if raw["type"] == "websocket.send":
            if "text" in raw and raw["text"] is not None:
                return str(raw["text"])
            if "bytes" in raw and raw["bytes"] is not None:
                return bytes(raw["bytes"])
        if raw["type"] == "websocket.close":
            code = raw.get("code", 1000)
            raise SimulatorTransportError(f"WebSocket closed by server with code {code}")
        raise SimulatorTransportError(f"Unexpected WebSocket event: {raw}")

    async def close(self, code: int = 1000) -> None:
        self._ws.close(code=code)
