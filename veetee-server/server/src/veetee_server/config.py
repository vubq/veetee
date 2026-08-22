"""Validated runtime configuration for the Veetee Server foundation."""

import json
import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Device Gateway Settings (M1.3 & M1.4 & M5)
    device_gateway_token: str = Field(default="")
    device_websocket_public_url: str = Field(default="")
    hello_timeout_seconds: float = Field(default=10.0, gt=0)
    idle_timeout_seconds: float = Field(default=60.0, gt=0)
    conversation_idle_timeout_seconds: float = Field(default=180.0, gt=0)
    conversation_playback_drain_seconds: float = Field(default=3.0, ge=0)
    ping_interval_seconds: float = Field(default=20.0, gt=0)
    pong_timeout_seconds: float = Field(default=10.0, gt=0)
    json_max_bytes: int = Field(default=16384, gt=0)
    json_max_depth: int = Field(default=8, gt=0)
    binary_max_bytes: int = Field(default=65536, gt=0)
    id_max_length: int = Field(default=128, gt=0)
    cleanup_timeout_seconds: float = Field(default=5.0, gt=0)

    # Device Activation & OTA Settings (M5)
    activation_ttl_seconds: int = Field(default=600, gt=0)
    activation_bind_receipt_ttl_seconds: int = Field(default=600, gt=0)
    activation_bind_rate_limit: int = Field(default=20, gt=0)
    activation_bind_rate_window_seconds: int = Field(default=600, gt=0)
    activation_console_url: str = Field(default="http://127.0.0.1:5173/devices")
    ota_artifact_dir: str = Field(default="/tmp/veetee_ota_artifacts")
    ota_public_base_url: str = Field(default="")
    ota_max_artifact_bytes: int = Field(default=16 * 1024 * 1024, gt=0)

    # Audio Primitives Settings (M1.5 & M2.7/M2.8)
    audio_codec: Literal["fake", "native"] = Field(default="fake")
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
    barge_in_pre_roll_frames: int = Field(default=5, gt=0, le=20)

    # VAD Provider & Silero VAD Settings (M2.1)
    vad_provider: Literal["fake", "silero_onnx"] = Field(default="fake")
    vad_model_path: str = Field(default="")
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_neg_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    vad_pre_roll_ms: int = Field(default=80, ge=0)
    vad_min_speech_ms: int = Field(default=250, ge=0)
    vad_end_silence_ms: int = Field(default=150, ge=0)
    vad_max_utterance_ms: int = Field(default=12000, gt=0)
    vad_max_concurrency: int = Field(default=4, gt=0)
    vad_admission_timeout_seconds: float = Field(default=2.0, gt=0)

    # ASR Provider & PhoWhisper Settings (M2.2)
    asr_provider: Literal["fake", "pho_whisper"] = Field(default="fake")
    asr_model_id: str = Field(default="mad1999/pho-whisper-small-ct2", min_length=1)
    asr_device: str = Field(default="cuda", min_length=1)
    asr_compute_type: str = Field(default="float16", min_length=1)
    asr_max_concurrency: int = Field(default=1, gt=0)
    asr_admission_timeout_seconds: float = Field(default=2.0, gt=0)
    asr_total_timeout_seconds: float = Field(default=10.0, gt=0)
    asr_max_audio_seconds: float = Field(default=30.0, gt=0)
    asr_language: str = Field(default="vi", min_length=1)
    asr_local_files_only: bool = Field(default=True)

    # LLM Provider & OmniRoute Groq Settings (M2.3)
    llm_provider: Literal["fake", "omniroute"] = Field(default="fake")
    llm_omniroute_base_url: str = Field(default="http://127.0.0.1:20128/v1", min_length=1)
    llm_api_key: str = Field(default="")
    llm_omniroute_model: str = Field(default="groq/openai/gpt-oss-120b", min_length=1)
    llm_omniroute_reasoning_effort: Literal["none", "low", "medium", "high"] = Field(
        default="low"
    )
    llm_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    llm_first_token_timeout_seconds: float = Field(default=5.0, gt=0)
    llm_total_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_concurrency: int = Field(default=4, gt=0)
    llm_admission_timeout_seconds: float = Field(default=2.0, gt=0)
    llm_circuit_breaker_failure_threshold: int = Field(default=3, gt=0)
    llm_circuit_breaker_cooldown_seconds: float = Field(default=10.0, gt=0)
    llm_max_response_bytes: int = Field(default=1048576, gt=0)

    # TTS Provider & Gemini Native TTS Settings (M2.5)
    tts_provider: Literal["fake", "gemini", "vieneu"] = Field(default="fake")
    tts_gemini_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    tts_gemini_main_model: str = Field(default="gemini-3.1-flash-tts-preview", min_length=1)
    tts_gemini_fallback_model: str = Field(default="gemini-2.5-flash-preview-tts", min_length=1)
    tts_enable_fallback_model: bool = Field(default=False)
    tts_gemini_voice: str = Field(default="Kore", min_length=1)
    tts_gemini_prompt_prefix: str = Field(default="")
    tts_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    tts_first_audio_timeout_seconds: float = Field(default=5.0, gt=0)
    tts_total_timeout_seconds: float = Field(default=30.0, gt=0)
    tts_max_concurrency: int = Field(default=4, gt=0)
    tts_admission_timeout_seconds: float = Field(default=2.0, gt=0)
    tts_circuit_breaker_failure_threshold: int = Field(default=3, gt=0)
    tts_circuit_breaker_cooldown_seconds: float = Field(default=10.0, gt=0)
    tts_max_retry_after_seconds: float = Field(default=60.0, gt=0)
    tts_max_response_bytes: int = Field(default=8388608, gt=0)
    vieneu_base_url: str = Field(default="http://127.0.0.1:23999", min_length=1)
    vieneu_timeout_seconds: float = Field(default=8.0, gt=0)

    tts_segment_first_min_chars: int = Field(default=24, gt=0)
    tts_segment_min_chars: int = Field(default=48, gt=0)
    tts_segment_max_chars: int = Field(default=220, gt=0)
    tts_segment_max_wait_seconds: float = Field(default=0.35, gt=0.0)

    # Brain AI / Mốc 3 Settings (M3.1 - M3.8)
    prompt_default_version: str = Field(default="v1.0.0", min_length=1)
    context_budget_max_tokens: int = Field(default=4096, gt=0)
    intent_strategy: Literal["direct_chat", "function_call", "intent_model"] = Field(
        default="function_call"
    )
    tool_execution_timeout_seconds: float = Field(default=5.0, gt=0)
    tool_max_output_chars: int = Field(default=2048, gt=0)
    memory_min_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    memory_recency_decay_half_life_hours: float = Field(default=24.0, gt=0)
    agent_snapshot_timeout_seconds: float = Field(default=2.0, gt=0)
    # Control-plane auth hardening (M4/M5 audit): login quota is persisted per
    # redacted identifier hash in PostgreSQL with a sliding time window.
    login_rate_limit: int = Field(default=10, gt=0)
    login_rate_window_seconds: int = Field(default=300, gt=0)
    persistence_enabled: bool = False
    database_dsn: str = Field(default="dbname=veetee", min_length=1)
    bootstrap_admin_email: str = Field(default="")
    bootstrap_admin_password: str = Field(default="", repr=False)
    cors_allowed_origins: str = Field(
        default="http://127.0.0.1:5173,http://localhost:5173"
    )

    @field_validator("tts_gemini_api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, v: object) -> list[str]:
        values: list[str]
        if isinstance(v, str):
            v_str = v.strip()
            if not v_str:
                return []
            if v_str.startswith("[") and v_str.endswith("]"):
                try:
                    parsed = json.loads(v_str)
                    if isinstance(parsed, list):
                        values = [str(key).strip() for key in parsed if str(key).strip()]
                        return list(dict.fromkeys(values))
                except json.JSONDecodeError:
                    pass
            values = [key.strip() for key in re.split(r"[,; \n\t]+", v_str) if key.strip()]
            return list(dict.fromkeys(values))
        if isinstance(v, list):
            values = [str(key).strip() for key in v if str(key).strip()]
            return list(dict.fromkeys(values))
        return []

    @field_validator("device_websocket_public_url")
    @classmethod
    def _validate_public_url(cls, v: str) -> str:
        if not v or not v.strip():
            return ""
        valid, reason = validate_device_websocket_url(v.strip())
        if not valid:
            raise ValueError(f"Invalid device_websocket_public_url: {reason}")
        return v.strip()

    @field_validator("activation_console_url", "ota_public_base_url")
    @classmethod
    def _validate_http_url(cls, value: str) -> str:
        if not value.strip():
            return ""
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("URL must use http/https and include a host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("URL must not contain credentials, query or fragment")
        return value.strip().rstrip("/")

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
        if self.vad_neg_threshold > self.vad_threshold:
            raise ValueError("vad_neg_threshold must not exceed vad_threshold")
        if self.vad_max_utterance_ms < self.vad_min_speech_ms:
            raise ValueError("vad_max_utterance_ms must be at least vad_min_speech_ms")
        if self.vad_provider == "silero_onnx" and not self.vad_model_path.strip():
            raise ValueError("vad_model_path must be specified when vad_provider is silero_onnx")
        if self.asr_total_timeout_seconds <= self.asr_admission_timeout_seconds:
            raise ValueError(
                "asr_total_timeout_seconds must be strictly greater than "
                "asr_admission_timeout_seconds"
            )
        if self.llm_provider == "omniroute" and not self.llm_omniroute_base_url.strip():
            raise ValueError(
                "llm_omniroute_base_url must be specified when llm_provider is omniroute"
            )
        if self.llm_total_timeout_seconds <= self.llm_connect_timeout_seconds:
            raise ValueError(
                "llm_total_timeout_seconds must be strictly greater than "
                "llm_connect_timeout_seconds"
            )
        if self.llm_total_timeout_seconds <= self.llm_first_token_timeout_seconds:
            raise ValueError(
                "llm_total_timeout_seconds must be strictly greater than "
                "llm_first_token_timeout_seconds"
            )
        if self.tts_provider == "gemini" and not self.tts_gemini_api_keys:
            raise ValueError("tts_gemini_api_keys must be specified when tts_provider is gemini")
        if self.tts_total_timeout_seconds <= self.tts_connect_timeout_seconds:
            raise ValueError(
                "tts_total_timeout_seconds must be greater than tts_connect_timeout_seconds"
            )
        if self.tts_total_timeout_seconds <= self.tts_first_audio_timeout_seconds:
            raise ValueError(
                "tts_total_timeout_seconds must be greater than tts_first_audio_timeout_seconds"
            )
        if self.tts_segment_max_chars < max(
            self.tts_segment_first_min_chars, self.tts_segment_min_chars
        ):
            raise ValueError(
                "tts_segment_max_chars must cover both tts_segment_first_min_chars and "
                "tts_segment_min_chars"
            )
        if self.persistence_enabled and not self.ota_public_base_url:
            raise ValueError(
                "ota_public_base_url is required when persistence is enabled"
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
