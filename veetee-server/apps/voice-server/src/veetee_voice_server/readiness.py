from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    healthy: bool
    required: bool
    detail: str | None = None


class ReadinessCheck(Protocol):
    async def __call__(self) -> ComponentHealth: ...


class ReadinessRegistry:
    def __init__(self) -> None:
        self._checks: list[ReadinessCheck] = []

    def register(self, check: ReadinessCheck) -> None:
        self._checks.append(check)

    def clear(self) -> None:
        self._checks.clear()

    async def snapshot(self) -> tuple[bool, list[ComponentHealth]]:
        components = [await check() for check in self._checks]
        ready = all(item.healthy for item in components if item.required)
        return ready, components
