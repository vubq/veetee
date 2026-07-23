from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import pytest

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
)
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.providers.tools import (
    CompositeToolBroker,
    RegistryToolBroker,
    ToolSpec,
)
from veetee_voice_server.tools.context import with_session_context_tools

pytestmark = pytest.mark.asyncio


def operation_context(token: CancellationToken | None = None) -> OperationContext:
    return OperationContext(
        "session-1",
        "turn-1",
        1,
        token or CancellationToken(),
        monotonic() + 2.0,
    )


def profile() -> SessionProfile:
    settings = Settings(environment="test", _env_file=None)  # type: ignore[call-arg]
    base = SessionProfile.defaults(settings)
    return replace(
        base,
        agent_name="VeeTee Lab",
        config_version=7,
        device_locale="vi-VN",
        device_time_zone="Asia/Bangkok",
        device_time_zone_offset_minutes=420,
    )


async def test_context_tools_merge_with_downstream_catalog_and_are_idempotent() -> None:
    async def downstream_handler(
        _: dict[str, Any],
        context: OperationContext,
    ) -> dict[str, bool]:
        context.checkpoint()
        return {"ok": True}

    downstream = RegistryToolBroker(
        [
            ToolSpec(
                name="fixture.read",
                description="Read a fixture value.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                handler=downstream_handler,
            )
        ]
    )
    broker = with_session_context_tools(profile(), downstream)

    assert [item["name"] for item in broker.list_tools()] == [
        "context.get_time",
        "context.get_session",
        "fixture.read",
    ]
    assert with_session_context_tools(profile(), broker) is broker
    assert await broker.call("fixture.read", {}, operation_context()) == {
        "tool": "fixture.read",
        "arguments": {},
        "result": {"ok": True},
    }


async def test_get_time_uses_device_zone_and_supports_explicit_iana_zone() -> None:
    fixed_now = datetime(2026, 7, 24, 18, 0, 0, tzinfo=UTC)
    broker = with_session_context_tools(
        profile(),
        RegistryToolBroker(),
        clock=lambda: fixed_now,
    )

    local = await broker.call("context.get_time", {}, operation_context())
    assert local["result"] == {
        "date": "2026-07-25",
        "time": "01:00:00",
        "date_time": "2026-07-25T01:00:00+07:00",
        "day_of_week": "saturday",
        "time_zone": "Asia/Bangkok",
        "utc_offset_minutes": 420,
        "unix_time_ms": 1784916000000,
    }

    utc = await broker.call(
        "context.get_time",
        {"time_zone": "UTC"},
        operation_context(),
    )
    assert utc["result"]["date_time"] == "2026-07-24T18:00:00+00:00"
    assert utc["result"]["day_of_week"] == "friday"
    assert utc["result"]["utc_offset_minutes"] == 0


async def test_context_tools_validate_arguments_and_hide_sensitive_identity() -> None:
    broker = with_session_context_tools(profile(), RegistryToolBroker())

    with pytest.raises(ValueError, match=r"[Aa]dditional properties"):
        await broker.call("context.get_session", {"unexpected": True}, operation_context())
    with pytest.raises(ValueError, match="Unknown IANA time zone"):
        await broker.call(
            "context.get_time",
            {"time_zone": "Mars/Olympus_Mons"},
            operation_context(),
        )

    session = await broker.call("context.get_session", {}, operation_context())
    assert session["result"] == {
        "agent_name": "VeeTee Lab",
        "agent_config_version": 7,
        "locale": "vi-VN",
        "interaction_mode": "auto",
        "device_locale": "vi-VN",
        "device_time_zone": "Asia/Bangkok",
        "effective_time_zone": "Asia/Bangkok",
    }
    assert "agent_id" not in session["result"]
    assert "tenant_id" not in session["result"]
    assert "device_id" not in session["result"]


async def test_context_tool_honors_turn_cancellation() -> None:
    token = CancellationToken()
    token.cancel("button_interrupt")
    broker = with_session_context_tools(profile(), RegistryToolBroker())

    with pytest.raises(TurnCancelledError):
        await broker.call("context.get_time", {}, operation_context(token))


async def test_composite_rejects_duplicate_tool_names() -> None:
    async def duplicate_handler(
        _: dict[str, Any],
        __: OperationContext,
    ) -> None:
        return None

    duplicate = RegistryToolBroker(
        [
            ToolSpec(
                name="context.get_time",
                description="Duplicate fixture.",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                handler=duplicate_handler,
            )
        ]
    )
    broker = with_session_context_tools(profile(), duplicate)

    with pytest.raises(ValueError, match="Duplicate composite MCP tool"):
        broker.list_tools()


async def test_composite_rejects_non_object_downstream_catalog() -> None:
    class MalformedBroker:
        def list_tools(self) -> list[dict[str, Any]]:
            return ["not-an-object"]  # type: ignore[list-item]

        async def call(
            self,
            _: str,
            __: dict[str, Any],
            ___: OperationContext,
        ) -> None:
            return None

    broker = CompositeToolBroker(MalformedBroker())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="non-object"):
        await broker.call("fixture.read", {}, operation_context())
