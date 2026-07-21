from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, HttpUrl
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

    nine_router_base_url: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:20128/v1"),
        validation_alias=AliasChoices("VEETEE_9ROUTER_BASE_URL", "VEETEE_NINE_ROUTER_BASE_URL"),
    )
    nine_router_api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices("VEETEE_9ROUTER_API_KEY", "VEETEE_NINE_ROUTER_API_KEY"),
    )
    nine_router_model: str = Field(
        default="cx/gpt-5.4-mini",
        validation_alias=AliasChoices("VEETEE_9ROUTER_MODEL", "VEETEE_NINE_ROUTER_MODEL"),
    )
    nine_router_reasoning_effort: Literal["none", "low", "medium", "high"] = Field(
        default="none",
        validation_alias=AliasChoices(
            "VEETEE_9ROUTER_REASONING_EFFORT", "VEETEE_NINE_ROUTER_REASONING_EFFORT"
        ),
    )

    models_root: Path = Path("models")
    asr_threads: int = Field(default=4, ge=1, le=8)
    tts_threads: int = Field(default=4, ge=1, le=8)
    tts_voice: str = "Ngọc Linh"
    tts_output_sample_rate: int = Field(default=24_000, ge=16_000, le=48_000)
    tts_apply_watermark: bool = True
    default_locale: str = "vi-VN"
    websocket_path: str = "/xiaozhi/v1/"
    input_sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    wire_sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    vad_threads: int = Field(default=1, ge=1, le=4)
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_release_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    vad_min_silence_ms: int = Field(default=400, ge=80, le=2_000)
    max_utterance_seconds: float = Field(default=20.0, gt=0.1, le=60.0)
    first_input_seconds: float = Field(default=15.0, gt=0.1, le=300.0)
    between_turns_seconds: float = Field(default=30.0, gt=0.1, le=600.0)
    closing_grace_seconds: float = Field(default=5.0, gt=0.1, le=60.0)
    asr_seconds: float = Field(default=8.0, gt=0.1, le=60.0)
    goodbye_text: str = "Tạm biệt, hẹn gặp lại."


@lru_cache
def get_settings() -> Settings:
    return Settings()
