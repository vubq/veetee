from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
)
from veetee_voice_server.transport.mcp import DeviceMcpClient


class DeviceSessionUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _Registration:
    registration_id: str
    mcp: DeviceMcpClient


class DeviceSessionRegistry:
    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._lock = asyncio.Lock()

    async def register(self, device_id: str, mcp: DeviceMcpClient) -> str:
        registration_id = uuid4().hex
        async with self._lock:
            self._registrations[device_id] = _Registration(registration_id, mcp)
        return registration_id

    async def unregister(self, device_id: str, registration_id: str) -> None:
        async with self._lock:
            current = self._registrations.get(device_id)
            if current is not None and current.registration_id == registration_id:
                self._registrations.pop(device_id, None)

    async def tools(self, device_id: str, *, timeout_seconds: float) -> list[dict[str, Any]]:
        registration = await self._get(device_id)
        return await registration.mcp.manager_tools(timeout_seconds)

    async def call(
        self,
        device_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool,
        timeout_seconds: float,
    ) -> Any:
        registration = await self._get(device_id)
        context = OperationContext(
            session_id=f"manager:{device_id}",
            turn_id=f"manager-mcp:{uuid4().hex}",
            generation=0,
            token=CancellationToken(),
            deadline_at=monotonic() + timeout_seconds,
        )
        return await registration.mcp.manager_call(
            name,
            arguments,
            confirmed=confirmed,
            context=context,
        )

    async def _get(self, device_id: str) -> _Registration:
        async with self._lock:
            registration = self._registrations.get(device_id)
        if registration is None:
            raise DeviceSessionUnavailableError("Device has no active voice session")
        return registration
