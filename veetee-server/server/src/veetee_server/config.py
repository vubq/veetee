"""Validated runtime configuration for the Veetee Server foundation."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Device Gateway Settings (M1.3)
    device_gateway_token: str = Field(default="")
    hello_timeout_seconds: float = Field(default=10.0, gt=0)
    idle_timeout_seconds: float = Field(default=60.0, gt=0)
    ping_interval_seconds: float = Field(default=20.0, gt=0)
    pong_timeout_seconds: float = Field(default=10.0, gt=0)
    json_max_bytes: int = Field(default=16384, gt=0)
    json_max_depth: int = Field(default=8, gt=0)
    binary_max_bytes: int = Field(default=65536, gt=0)
    id_max_length: int = Field(default=128, gt=0)
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
