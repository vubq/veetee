"""Validated runtime configuration for the Veetee Server foundation."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Veetee audio contract frame duration used in device hello negotiation.
# Audio queue duration settings must always be able to hold at least one frame.
_AUDIO_FRAME_DURATION_MS = 60.0


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

    # Audio Primitives Settings (M1.5)
    audio_max_queue_items: int = Field(default=100, gt=0)
    audio_max_queue_bytes: int = Field(default=1048576, gt=0)
    audio_max_queue_duration_ms: float = Field(default=10000.0, gt=0)
    audio_pacing_max_drift_ms: float = Field(default=100.0, gt=0)

    # Fake AI Pipeline Settings (M1.6)
    pipeline_vad_speech_threshold: float = Field(default=32.0, ge=0.0)
    pipeline_vad_start_frames: int = Field(default=2, gt=0)
    pipeline_vad_end_silence_frames: int = Field(default=3, gt=0)
    pipeline_max_utterance_frames: int = Field(default=200, gt=0)
    pipeline_tts_chunks_per_sentence: int = Field(default=3, gt=0)

    @field_validator("device_websocket_public_url")
    @classmethod
    def _validate_public_url(cls, v: str) -> str:
        if not v or not v.strip():
            return ""
        valid, reason = validate_device_websocket_url(v.strip())
        if not valid:
            raise ValueError(f"Invalid device_websocket_public_url: {reason}")
        return v.strip()

    @model_validator(mode="after")
    def _validate_audio_constraints(self) -> "Settings":
        # The audio queue must be able to hold at least one full 60 ms frame;
        # a smaller budget makes every frame unschedulable by construction.
        if self.audio_max_queue_duration_ms < _AUDIO_FRAME_DURATION_MS:
            raise ValueError(
                "audio_max_queue_duration_ms must be at least "
                f"{_AUDIO_FRAME_DURATION_MS:.0f} ms (one audio frame)"
            )
        # Pacing drift budget must stay below the queue duration budget so a
        # pacer reset can never exceed what the queue can buffer.
        if self.audio_pacing_max_drift_ms >= self.audio_max_queue_duration_ms:
            raise ValueError(
                "audio_pacing_max_drift_ms must be smaller than audio_max_queue_duration_ms"
            )
        # The VAD needs room to actually start a speech segment before the
        # maximum utterance length forces it to close.
        if self.pipeline_max_utterance_frames < self.pipeline_vad_start_frames:
            raise ValueError(
                "pipeline_max_utterance_frames must be at least pipeline_vad_start_frames"
            )
        return self


def get_effective_device_websocket_url(settings: Settings) -> str:
    """Returns configured public websocket URL or derives local default."""
    if settings.device_websocket_public_url.strip():
        return settings.device_websocket_public_url.strip()
    return f"ws://{settings.host}:{settings.port}/api/v1/devices/ws"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
