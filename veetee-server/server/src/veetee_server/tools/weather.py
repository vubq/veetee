"""Open-Meteo weather lookup routed through the guarded external MCP client.

Decision (locked, M6.6): the weather adapter uses Open-Meteo geocoding plus
forecast over HTTPS via :class:`ExternalMCPClient.get_json`, so every hop
inherits the host allowlist, SSRF checks, timeouts and response bounds. All
remote values pass through a strict bounded schema before leaving this module;
tests inject a fake sender and never touch the internet.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from veetee_server.tools.external_mcp import ExternalMCPClient
from veetee_server.tools.registry import ToolDefinition

GEOCODING_URL_TEMPLATE = (
    "https://geocoding-api.open-meteo.com/v1/search"
    "?name={name}&count=1&language=vi&format=json"
)
FORECAST_URL_TEMPLATE = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={latitude}&longitude={longitude}"
    "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
)

# WMO interpretation codes reduced to short Vietnamese descriptions.
WEATHER_CODES: dict[int, str] = {
    0: "Trời quang",
    1: "Chủ yếu quang đãng",
    2: "Có mây rải rác",
    3: "Nhiều mây",
    45: "Sương mù",
    48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn vừa",
    55: "Mưa phùn dày",
    56: "Mưa phùn đóng băng nhẹ",
    57: "Mưa phùn đóng băng dày",
    61: "Mưa nhẹ",
    63: "Mưa vừa",
    65: "Mưa to",
    66: "Mưa đá nhẹ",
    67: "Mưa đá nặng",
    71: "Tuyết rơi nhẹ",
    73: "Tuyết rơi vừa",
    75: "Tuyết rơi dày",
    77: "Hạt tuyết",
    80: "Mưa rào nhẹ",
    81: "Mưa rào vừa",
    82: "Mưa rào dữ dội",
    85: "Mưa tuyết nhẹ",
    86: "Mưa tuyết dày",
    95: "Dông",
    96: "Dông kèm mưa đá nhẹ",
    99: "Dông kèm mưa đá nặng",
}

_MAX_DESCRIPTION_CHARS = 64


class WeatherLookupError(Exception):
    """Raised when location input or remote payload fails the bounded schema."""


@dataclass(frozen=True, slots=True)
class OpenMeteoConfig:
    """Bounded schema limits for weather lookups."""

    max_location_chars: int = 100
    max_name_chars: int = 120


def describe_weather_code(code: int) -> str:
    return WEATHER_CODES.get(code, "Thời tiết không xác định")[
        :_MAX_DESCRIPTION_CHARS
    ]


def _require_number(
    container: dict[str, Any], field: str, low: float, high: float
) -> float:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherLookupError(f"Weather field '{field}' must be a number")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise WeatherLookupError(f"Weather field '{field}' is not finite")
    if not low <= number <= high:
        raise WeatherLookupError(
            f"Weather field '{field}' out of bounds [{low}, {high}]"
        )
    return number


def _require_int(
    container: dict[str, Any], field: str, low: int, high: int
) -> int:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WeatherLookupError(f"Weather field '{field}' must be an integer")
    if not low <= value <= high:
        raise WeatherLookupError(
            f"Weather field '{field}' out of bounds [{low}, {high}]"
        )
    return value


def _require_bounded_str(
    container: dict[str, Any], field: str, max_chars: int
) -> str:
    value = container.get(field)
    if not isinstance(value, str):
        raise WeatherLookupError(f"Weather field '{field}' must be a string")
    text = value.strip()
    if not text or len(text) > max_chars:
        raise WeatherLookupError(
            f"Weather field '{field}' must be 1..{max_chars} characters"
        )
    return text


class OpenMeteoWeatherTool:
    """Geocoding + current forecast with a strict bounded output contract."""

    def __init__(
        self,
        client: ExternalMCPClient,
        *,
        config: OpenMeteoConfig | None = None,
        geocoding_url_template: str = GEOCODING_URL_TEMPLATE,
        forecast_url_template: str = FORECAST_URL_TEMPLATE,
    ) -> None:
        self._client = client
        self._config = config or OpenMeteoConfig()
        self._geocoding_url_template = geocoding_url_template
        self._forecast_url_template = forecast_url_template

    async def lookup(self, location: str) -> dict[str, Any]:
        """Resolves one place name then fetches its bounded current weather."""
        cleaned = location.strip()
        if not cleaned or len(cleaned) > self._config.max_location_chars:
            raise WeatherLookupError(
                f"Location must be 1..{self._config.max_location_chars} characters"
            )
        if any(ord(char) < 32 for char in cleaned):
            raise WeatherLookupError("Location contains control characters")

        geocode_url = self._geocoding_url_template.format(name=quote(cleaned, safe=""))
        geocode = await self._client.get_json(geocode_url)
        latitude, longitude, place_name = self._parse_geocode(geocode)

        forecast_url = self._forecast_url_template.format(
            latitude=format(latitude, ".6f"), longitude=format(longitude, ".6f")
        )
        forecast = await self._client.get_json(forecast_url)
        return self._build_result(forecast, place_name, latitude, longitude)

    # -------------------------------------------------------------- parsing

    def _parse_geocode(self, payload: dict[str, Any]) -> tuple[float, float, str]:
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise WeatherLookupError("Geocoding result set is empty or malformed")
        first = results[0]
        if not isinstance(first, dict):
            raise WeatherLookupError("Geocoding entry must be an object")
        latitude = _require_number(first, "latitude", -90.0, 90.0)
        longitude = _require_number(first, "longitude", -180.0, 180.0)
        name = _require_bounded_str(first, "name", self._config.max_name_chars)
        return latitude, longitude, name

    def _build_result(
        self,
        payload: dict[str, Any],
        place_name: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any]:
        current = payload.get("current")
        if not isinstance(current, dict):
            raise WeatherLookupError("Forecast 'current' section is missing")
        temperature = _require_number(current, "temperature_2m", -90.0, 60.0)
        humidity = _require_int(current, "relative_humidity_2m", 0, 100)
        weather_code = _require_int(current, "weather_code", 0, 99)
        wind_speed = _require_number(current, "wind_speed_10m", 0.0, 400.0)
        return {
            "location": place_name,
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
            "temperature_celsius": temperature,
            "humidity_percent": humidity,
            "wind_speed_kmh": wind_speed,
            "condition": describe_weather_code(weather_code),
        }


def create_weather_tool_definition(tool: OpenMeteoWeatherTool) -> ToolDefinition:
    """Wraps the Open-Meteo lookup as a registry tool definition."""

    async def handler(args: dict[str, Any], context: object) -> dict[str, Any]:
        del context  # weather lookup needs no device/session binding
        location = args.get("location")
        if not isinstance(location, str):
            raise WeatherLookupError("Argument 'location' must be a string")
        return await tool.lookup(location)

    handler.__name__ = "_handle_external_weather_lookup"

    return ToolDefinition(
        name="external.weather_lookup",
        description=(
            "Tra cứu thời tiết hiện tại qua Open-Meteo: geocoding địa điểm rồi "
            "lấy nhiệt độ, độ ẩm, gió và mô tả thời tiết."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Tên thành phố/địa điểm cần tra cứu",
                }
            },
            "required": ["location"],
        },
        version="v1.0.0",
        requires_confirmation=False,
        handler=cast_handler(handler),
    )


def cast_handler(
    handler: Callable[[dict[str, Any], object], Awaitable[Any]],
) -> Callable[[dict[str, Any], Any], Awaitable[Any]]:
    return handler
