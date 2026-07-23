from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.providers.contracts import ToolBroker
from veetee_voice_server.providers.tools import (
    CompositeToolBroker,
    RegistryToolBroker,
    ToolSpec,
)

Clock = Callable[[], datetime]

_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}
_TIME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "time_zone": {
            "type": "string",
            "minLength": 1,
            "maxLength": 80,
        }
    },
}


class SessionContextToolBroker(CompositeToolBroker):
    """Marker type that keeps session tools from being composed twice."""

    includes_session_context_tools = True


def with_session_context_tools(
    profile: SessionProfile,
    downstream: ToolBroker,
    *,
    clock: Clock | None = None,
) -> ToolBroker:
    if getattr(downstream, "includes_session_context_tools", False) is True:
        return downstream

    current_time = clock or (lambda: datetime.now(UTC))
    default_time_zone = _effective_time_zone(profile)

    async def get_time(
        arguments: dict[str, Any],
        context: OperationContext,
    ) -> dict[str, Any]:
        context.checkpoint()
        requested = arguments.get("time_zone")
        time_zone = requested if isinstance(requested, str) else default_time_zone
        zone = _zone(time_zone)
        current = current_time()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        local = current.astimezone(zone)
        offset = local.utcoffset()
        return {
            "date": local.date().isoformat(),
            "time": local.strftime("%H:%M:%S"),
            "date_time": local.isoformat(timespec="seconds"),
            "day_of_week": local.strftime("%A").lower(),
            "time_zone": time_zone,
            "utc_offset_minutes": (
                int(offset.total_seconds() // 60) if offset is not None else 0
            ),
            "unix_time_ms": int(current.timestamp() * 1_000),
        }

    async def get_session(
        _: dict[str, Any],
        context: OperationContext,
    ) -> dict[str, Any]:
        context.checkpoint()
        return {
            "agent_name": profile.agent_name,
            "agent_config_version": profile.config_version,
            "locale": profile.locale,
            "interaction_mode": profile.interaction_mode,
            "device_locale": profile.device_locale,
            "device_time_zone": profile.device_time_zone,
            "effective_time_zone": default_time_zone,
        }

    server_tools = RegistryToolBroker(
        [
            ToolSpec(
                name="context.get_time",
                description=(
                    "Read the current date, time, weekday and UTC offset in the "
                    "session time zone or another valid IANA time zone."
                ),
                input_schema=_TIME_SCHEMA,
                handler=get_time,
            ),
            ToolSpec(
                name="context.get_session",
                description=(
                    "Read the active assistant version, locale, interaction mode "
                    "and non-secret device locale/time-zone context."
                ),
                input_schema=_EMPTY_OBJECT_SCHEMA,
                handler=get_session,
            ),
        ]
    )
    return SessionContextToolBroker(server_tools, downstream)


def _effective_time_zone(profile: SessionProfile) -> str:
    if profile.prompt.time_zone_source == "device" and profile.device_time_zone:
        try:
            _zone(profile.device_time_zone)
        except ValueError:
            pass
        else:
            return profile.device_time_zone
    _zone(profile.prompt.time_zone)
    return profile.prompt.time_zone


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown IANA time zone: {value}") from error
