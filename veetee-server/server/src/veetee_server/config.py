"""Validated runtime configuration for the Veetee Server foundation."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def validate_device_websocket_url(url: str) -> tuple[bool, str | None]:
    """Validates device websocket public URL.

    Rules:
    - Scheme must be ws or wss.
    - Must not contain userinfo/credentials (username, password, @ in netloc).
    - Must not contain query string or fragment (no discovery/state parameters;
      the Veetee WS endpoint contract does not define query parameters).
    - Netloc/host must be non-empty; port, if present, must be 1-65535.
    """
    if not url or not isinstance(url, str) or not url.strip():
        return False, "URL must be a non-empty string"
    try:
        parsed = urlparse(url.strip())
        # Accessing hostname/port raises ValueError for malformed or
        # out-of-range ports; the whole parse must be guarded so callers get a
        # clean validation result instead of an uncaught exception.
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        if "Port out of range" in str(exc):
            return False, "URL port must be between 1 and 65535"
        return False, f"Invalid URL host or port: {exc}"
    except Exception as exc:
        return False, f"Failed to parse URL: {exc}"

    if parsed.scheme.lower() not in ("ws", "wss"):
        return False, "Invalid scheme: must be ws or wss"

    if parsed.username or parsed.password or "@" in parsed.netloc:
        return False, "URL must not contain userinfo or credentials"

    if parsed.query:
        return False, "URL must not contain query string"

    if parsed.fragment:
        return False, "URL must not contain fragment"

    if not parsed.netloc or not hostname:
        return False, "URL must contain a valid host"

    if port is not None and not (1 <= port <= 65535):
        return False, "URL port must be between 1 and 65535"

    if parsed.path != "/api/v1/devices/ws":
        return False, "URL path must be /api/v1/devices/ws"

    return True, None


class Settings(BaseSettings):
    """Environment-backed settings with the public VEETEE_ prefix."""

    model_config = SettingsConfigDict(
        env_prefix="VEETEE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="veetee-server", min_length=1, max_length=80)
    environment: str = Field(default="local", min_length=1, max_length=32)
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    readiness_enabled: bool = True

    # Device Gateway Settings (M1.3 & M1.4)
    device_gateway_token: str = Field(default="")
    device_websocket_public_url: str = Field(default="")
    hello_timeout_seconds: float = Field(default=10.0, gt=0)
    idle_timeout_seconds: float = Field(default=60.0, gt=0)
    ping_interval_seconds: float = Field(default=20.0, gt=0)
    pong_timeout_seconds: float = Field(default=10.0, gt=0)
    json_max_bytes: int = Field(default=16384, gt=0)
    json_max_depth: int = Field(default=8, gt=0)
    binary_max_bytes: int = Field(default=65536, gt=0)
    id_max_length: int = Field(default=128, gt=0)
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0)

    @field_validator("device_websocket_public_url")
    @classmethod
    def _validate_public_url(cls, v: str) -> str:
        if not v or not v.strip():
            return ""
        valid, reason = validate_device_websocket_url(v.strip())
        if not valid:
            raise ValueError(f"Invalid device_websocket_public_url: {reason}")
        return v.strip()


def get_effective_device_websocket_url(settings: Settings) -> str:
    """Returns configured public websocket URL or derives local default."""
    if settings.device_websocket_public_url.strip():
        return settings.device_websocket_public_url.strip()
    return f"ws://{settings.host}:{settings.port}/api/v1/devices/ws"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
