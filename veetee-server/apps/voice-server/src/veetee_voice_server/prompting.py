from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_PROMPT_PATH = Path(__file__).with_name("prompts") / "agent-base-prompt.txt"
DEFAULT_AGENT_BASE_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8").strip()

ALLOWED_PROMPT_VARIABLES = frozenset(
    {
        "agent_name",
        "language",
        "locale",
        "persona",
        "personality",
        "response_style",
        "user_address",
        "interaction_mode",
        "config_version",
        "current_date",
        "current_time",
        "timezone",
        "device_locale",
        "device_timezone",
        "device_timezone_offset",
        "available_tools",
    }
)
REQUIRED_PROMPT_VARIABLES = frozenset(
    {"agent_name", "language", "persona", "personality"}
)
_TOKEN_PATTERN = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\s*}}")


class PromptTemplateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptConfiguration:
    template: str
    language: str
    time_zone: str
    personality_preset_id: str
    personality: str
    response_style: str
    user_address: str
    time_zone_source: str = "device"

    @classmethod
    def defaults(
        cls,
        *,
        language: str,
        time_zone: str,
        personality: str,
        time_zone_source: str = "device",
    ) -> PromptConfiguration:
        return cls(
            template=DEFAULT_AGENT_BASE_PROMPT,
            language=language,
            time_zone=time_zone,
            time_zone_source=time_zone_source,
            personality_preset_id="local-default",
            personality=personality,
            response_style="",
            user_address="",
        )

    @classmethod
    def from_payload(
        cls,
        value: object,
        *,
        defaults: PromptConfiguration,
    ) -> PromptConfiguration:
        if not isinstance(value, dict):
            return defaults
        template = _required_string(value.get("template"), "template", 20_000)
        language = _required_string(value.get("language"), "language", 120)
        time_zone = _required_string(value.get("timeZone"), "timeZone", 80)
        time_zone_source = _optional_string(
            value.get("timeZoneSource"), "timeZoneSource", 16
        ) or "device"
        if time_zone_source not in {"device", "fixed"}:
            raise PromptTemplateError("Prompt timeZoneSource must be device or fixed")
        personality_preset_id = _required_string(
            value.get("personalityPresetId"), "personalityPresetId", 80
        )
        personality = _required_string(value.get("personality"), "personality", 8_000)
        response_style = _optional_string(value.get("responseStyle"), "responseStyle", 2_000)
        user_address = _optional_string(value.get("userAddress"), "userAddress", 120)
        validate_prompt_template(template)
        _zone(time_zone)
        return cls(
            template=template,
            language=language,
            time_zone=time_zone,
            time_zone_source=time_zone_source,
            personality_preset_id=personality_preset_id,
            personality=personality,
            response_style=response_style,
            user_address=user_address,
        )

    def render(
        self,
        *,
        agent_name: str,
        locale: str,
        persona: str,
        interaction_mode: str,
        config_version: int,
        tools: list[dict[str, Any]],
        device_locale: str | None = None,
        device_time_zone: str | None = None,
        device_time_zone_offset_minutes: int | None = None,
        now: datetime | None = None,
    ) -> str:
        effective_time_zone = self.time_zone
        if self.time_zone_source == "device" and device_time_zone:
            try:
                _zone(device_time_zone)
            except PromptTemplateError:
                pass
            else:
                effective_time_zone = device_time_zone
        zone = _zone(effective_time_zone)
        current = now.astimezone(zone) if now is not None else datetime.now(zone)
        current_offset = current.utcoffset()
        offset_minutes = (
            device_time_zone_offset_minutes
            if self.time_zone_source == "device"
            and device_time_zone_offset_minutes is not None
            else int(current_offset.total_seconds() // 60) if current_offset else 0
        )
        values = {
            "agent_name": agent_name,
            "language": self.language,
            "locale": device_locale or locale,
            "persona": persona,
            "personality": self.personality,
            "response_style": self.response_style,
            "user_address": self.user_address,
            "interaction_mode": interaction_mode,
            "config_version": str(config_version),
            "current_date": current.strftime("%Y-%m-%d"),
            "current_time": current.strftime("%H:%M:%S"),
            "timezone": effective_time_zone,
            "device_locale": device_locale or locale,
            "device_timezone": effective_time_zone,
            "device_timezone_offset": _format_offset(offset_minutes),
            "available_tools": _bounded_tool_catalog(tools),
        }
        return render_prompt_template(self.template, values)


def validate_prompt_template(template: str) -> tuple[str, ...]:
    if not template or len(template) > 20_000:
        raise PromptTemplateError("Prompt template must contain 1 to 20000 characters")
    if "{%" in template or "{#" in template or "{{{" in template:
        raise PromptTemplateError(
            "Prompt template supports only simple allowlisted {{variable}} tokens"
        )
    variables: list[str] = []

    def replace_token(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in ALLOWED_PROMPT_VARIABLES:
            raise PromptTemplateError(f"Unknown prompt template variable: {name}")
        if name not in variables:
            variables.append(name)
        return ""

    stripped = _TOKEN_PATTERN.sub(replace_token, template)
    if "{{" in stripped or "}}" in stripped:
        raise PromptTemplateError("Prompt template contains a malformed variable token")
    missing = sorted(REQUIRED_PROMPT_VARIABLES.difference(variables))
    if missing:
        raise PromptTemplateError(
            "Prompt template is missing required variables: " + ", ".join(missing)
        )
    return tuple(variables)


def render_prompt_template(template: str, values: dict[str, str]) -> str:
    variables = validate_prompt_template(template)
    missing = [name for name in variables if name not in values]
    if missing:
        raise PromptTemplateError(
            "Prompt runtime values are missing: " + ", ".join(sorted(missing))
        )

    def replace_token(match: re.Match[str]) -> str:
        return values[match.group(1)]

    rendered = _TOKEN_PATTERN.sub(replace_token, template).strip()
    if "{{" in rendered or "}}" in rendered:
        raise PromptTemplateError("Prompt rendering left an unresolved variable")
    return rendered


def _bounded_tool_catalog(tools: list[dict[str, Any]]) -> str:
    catalog: list[dict[str, str]] = []
    for tool in tools[:32]:
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = tool.get("description")
        catalog.append(
            {
                "name": name.strip()[:160],
                "description": (
                    " ".join(description.split())[:300]
                    if isinstance(description, str)
                    else ""
                ),
            }
        )
    encoded = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    return encoded[:8_000]


def _required_string(value: object, field: str, maximum: int) -> str:
    normalized = _optional_string(value, field, maximum)
    if not normalized:
        raise PromptTemplateError(f"Prompt {field} is required")
    return normalized


def _optional_string(value: object, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise PromptTemplateError(f"Prompt {field} must be a string up to {maximum} characters")
    return value.strip()


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise PromptTemplateError(f"Unknown prompt time zone: {value}") from error


def _format_offset(minutes: int) -> str:
    bounded = max(-840, min(840, int(minutes)))
    sign = "+" if bounded >= 0 else "-"
    absolute = abs(bounded)
    return f"UTC{sign}{absolute // 60:02d}:{absolute % 60:02d}"
