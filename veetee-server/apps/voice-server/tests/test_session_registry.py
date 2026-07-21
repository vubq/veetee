from __future__ import annotations

from typing import Any

import pytest

from veetee_voice_server.transport.session_registry import (
    DeviceSessionRegistry,
    DeviceSessionUnavailableError,
)

pytestmark = pytest.mark.asyncio


class FakeMcp:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    async def manager_tools(self, _: float) -> list[dict[str, Any]]:
        return [{"name": self.name}]

    async def manager_call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool,
        context: object,
    ) -> dict[str, Any]:
        self.calls.append((name, arguments, confirmed))
        return {"tool": name, "arguments": arguments, "confirmed": confirmed}


async def test_latest_session_wins_without_stale_unregister_race() -> None:
    registry = DeviceSessionRegistry()
    first = FakeMcp("self.first")
    second = FakeMcp("self.second")

    first_registration = await registry.register("device-1", first)  # type: ignore[arg-type]
    second_registration = await registry.register("device-1", second)  # type: ignore[arg-type]
    await registry.unregister("device-1", first_registration)

    assert await registry.tools("device-1", timeout_seconds=1.0) == [{"name": "self.second"}]
    result = await registry.call(
        "device-1",
        "self.second",
        {"value": 1},
        confirmed=True,
        timeout_seconds=1.0,
    )
    assert result["confirmed"] is True
    assert second.calls == [("self.second", {"value": 1}, True)]

    await registry.unregister("device-1", second_registration)
    with pytest.raises(DeviceSessionUnavailableError):
        await registry.tools("device-1", timeout_seconds=1.0)
