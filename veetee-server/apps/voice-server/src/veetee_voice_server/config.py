from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VEETEE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    reload: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = False
    readiness_probe_external: bool = False

    redis_url: str = "redis://127.0.0.1:6379/0"
    manager_api_url: HttpUrl = HttpUrl("http://127.0.0.1:8001")

    nine_router_base_url: HttpUrl = HttpUrl("http://127.0.0.1:20128/v1")
    nine_router_api_key: str = Field(default="", repr=False)
    nine_router_model: str = "cx/gpt-5.4-mini"
    nine_router_reasoning_effort: Literal["none", "low", "medium", "high"] = "none"


@lru_cache
def get_settings() -> Settings:
    return Settings()
