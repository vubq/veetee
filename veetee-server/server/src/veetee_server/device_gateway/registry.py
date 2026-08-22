"""In-memory session registry for managing active WebSocket device sessions."""

import asyncio
import logging

from fastapi import WebSocket

from veetee_server.domain.session import DeviceSession

logger = logging.getLogger("veetee.device_gateway")


class DeviceSessionRegistry:
    """Thread-safe and asyncio-safe registry tracking active device sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[DeviceSession, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: DeviceSession, websocket: WebSocket) -> None:
        async with self._lock:
            self._sessions[str(session.id)] = (session, websocket)

    async def unregister(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def get(self, session_id: str) -> tuple[DeviceSession, WebSocket] | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def find_device_sessions(self, device_id: str) -> list[tuple[DeviceSession, WebSocket]]:
        """Returns every live session currently advertising a persisted device id."""
        async with self._lock:
            return [
                (session, websocket)
                for session, websocket in self._sessions.values()
                if session.device_id == device_id
            ]

    async def online_device_ids(self) -> set[str]:
        async with self._lock:
            return {session.device_id for session, _ in self._sessions.values()}

    async def close_device(
        self, device_id: str, code: int = 1008, reason: str = "Device unbound"
    ) -> int:
        """Revokes every active connection for a device after unbind."""
        async with self._lock:
            matched = [
                (session_id, session, websocket)
                for session_id, (session, websocket) in self._sessions.items()
                if session.device_id == device_id
            ]
            for session_id, _, _ in matched:
                self._sessions.pop(session_id, None)
        for _, session, websocket in matched:
            try:
                await session.close()
            except Exception:
                logger.exception(
                    "error_closing_unbound_device_session",
                    extra={"context": {"session_id": str(session.id)}},
                )
            try:
                await websocket.close(code=code, reason=reason)
            except Exception:
                pass
        return len(matched)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    async def close_all(self, code: int = 1012, reason: str = "Server shutdown") -> None:
        """Closes all active sessions and WebSockets gracefully during shutdown."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session, ws in sessions:
            try:
                await session.close()
            except Exception:
                logger.exception(
                    "error_closing_session_during_shutdown",
                    extra={"context": {"session_id": str(session.id)}},
                )
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass
