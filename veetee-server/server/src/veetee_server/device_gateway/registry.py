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

    async def online_device_ids(self) -> set[str]:
        async with self._lock:
            return {session.device_id for session, _ in self._sessions.values()}

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
