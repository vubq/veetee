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
    manager_internal_token: str = Field(default="", repr=False)
    manager_request_seconds: float = Field(default=3.0, gt=0.1, le=15.0)
    telemetry_queue_capacity: int = Field(default=256, ge=32, le=2_048)
    telemetry_batch_size: int = Field(default=32, ge=1, le=64)
    telemetry_flush_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    telemetry_shutdown_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    memory_queue_capacity: int = Field(default=128, ge=8, le=2_048)
    memory_shutdown_seconds: float = Field(default=1.0, ge=0.1, le=5.0)
    require_device_auth: bool = True

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
        default="cx/gpt-5.6-terra",
        validation_alias=AliasChoices("VEETEE_9ROUTER_MODEL", "VEETEE_NINE_ROUTER_MODEL"),
    )
    nine_router_reasoning_effort: Literal["none", "low", "medium", "high"] = Field(
        default="none",
        validation_alias=AliasChoices(
            "VEETEE_9ROUTER_REASONING_EFFORT", "VEETEE_NINE_ROUTER_REASONING_EFFORT"
        ),
    )
    groq_cloud_api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices("VEETEE_GROQ_CLOUD_API_KEY", "GROQ_API_KEY"),
    )
    cliproxy_base_url: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:8317/v1"),
        validation_alias=AliasChoices(
            "VEETEE_CLIPROXY_BASE_URL",
            "VEETEE_CLIPROXYAPI_BASE_URL",
        ),
    )
    cliproxy_api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices(
            "VEETEE_CLIPROXY_API_KEY",
            "VEETEE_CLIPROXYAPI_API_KEY",
        ),
    )
    cliproxy_model: str = Field(
        default="gpt-5.6-terra",
        validation_alias=AliasChoices(
            "VEETEE_CLIPROXY_MODEL",
            "VEETEE_CLIPROXYAPI_MODEL",
        ),
    )
    llm_prewarm: bool = True
    llm_prewarm_seconds: float = Field(default=12.0, gt=0.1, le=30.0)
    planner_seconds: float = Field(default=15.0, gt=0.5, le=15.0)

    models_root: Path = Path("models")
    asr_threads: int = Field(default=2, ge=1, le=8)
    tts_threads: int = Field(default=2, ge=1, le=8)
    tts_backend: Literal["onnx", "native"] = "onnx"
    tts_voice: str = "Trúc Ly"
    tts_style: Literal["tu_nhien", "doc_truyen", "tin_tuc"] = "tu_nhien"
    tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    tts_stream_leadin_frames: int = Field(default=16, ge=4, le=25)
    tts_output_sample_rate: int = Field(default=24_000, ge=16_000, le=48_000)
    tts_apply_watermark: bool = True
    tts_native_model_dir: Path = Path("models/vieneu-v3-turbo-native")
    tts_native_library_path: Path = Path(
        ".cache/local-ai/VieNeu-TTS.cpp/build-cpu/libvieneu-tts.so"
    )
    tts_native_realtime_headroom: float = Field(default=1.15, ge=1.0, le=2.0)
    tts_native_use_ref_codes: bool = True
    tts_playback_queue_seconds: float = Field(default=5.0, ge=1.0, le=15.0)
    media_provider: Literal["disabled", "youtube_music"] = "disabled"
    media_search_results: int = Field(default=8, ge=1, le=12)
    media_search_seconds: float = Field(default=15.0, ge=1.0, le=30.0)
    media_pcm_chunk_ms: int = Field(default=60, ge=20, le=200)
    media_max_audio_chunks: int = Field(default=240_000, ge=1_000, le=1_000_000)
    media_max_audio_bytes: int = Field(
        default=512 * 1_024 * 1_024,
        ge=16 * 1_024 * 1_024,
        le=2 * 1_024 * 1_024 * 1_024,
    )
    media_process_shutdown_seconds: float = Field(default=3.0, ge=0.5, le=10.0)
    media_ffmpeg_binary: str = Field(default="ffmpeg", min_length=1, max_length=240)
    media_youtube_cookie_file: str = Field(default="", max_length=1_024, repr=False)
    default_locale: str = "vi-VN"
    default_persona: str = ""
    default_agent_name: str = "VeeTee"
    default_prompt_language: str = "Tiếng Việt"
    default_prompt_timezone: str = "Asia/Bangkok"
    default_personality: str = (
        "Ấm áp, tự nhiên, biết lắng nghe và phản hồi thẳng vào điều người dùng quan tâm."
    )
    websocket_path: str = "/veetee/v1/"
    lab_websocket_path: str = "/veetee/lab/v1/"
    lab_allowed_origins: str = "http://127.0.0.1:8081,http://localhost:8081"
    lab_max_sessions: int = Field(default=4, ge=1, le=32)
    input_sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    input_frame_duration_ms: int = Field(default=60, ge=20, le=60, multiple_of=20)
    wire_sample_rate: int = Field(default=24_000, ge=8_000, le=48_000)
    wire_frame_duration_ms: int = Field(default=60, ge=20, le=60, multiple_of=20)
    hello_timeout_seconds: float = Field(default=10.0, gt=0.1, le=30.0)
    vad_threads: int = Field(default=1, ge=1, le=4)
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    vad_release_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    vad_min_silence_ms: int = Field(default=400, ge=80, le=2_000)
    vad_fast_silence_ms: int = Field(default=320, ge=64, le=1_000)
    vad_fast_endpoint_min_speech_ms: int = Field(default=640, ge=160, le=4_000)
    vad_quiet_probability: float = Field(default=0.15, ge=0.0, le=1.0)
    vad_pre_roll_ms: int = Field(default=320, ge=0, le=1_000)
    # A usable device-mic turn must have at least two independent supports:
    # near-field level, signal-to-noise separation, or dense VAD speech.
    admission_min_signal_supports: int = Field(default=2, ge=1, le=3)
    admission_strong_signal_rms_dbfs: float = Field(default=-28.0, ge=-80.0, le=0.0)
    admission_clean_snr_db: float = Field(default=8.0, ge=-20.0, le=40.0)
    admission_dense_vad_mean_probability: float = Field(default=0.55, ge=0.0, le=1.0)
    admission_dense_vad_speech_ratio: float = Field(default=0.55, ge=0.0, le=1.0)
    admission_short_transcript_characters: int = Field(default=3, ge=1, le=20)
    admission_short_utterance_ms: int = Field(default=1_200, ge=200, le=3_000)
    admission_short_min_signal_supports: int = Field(default=3, ge=1, le=3)
    admission_contextual_vad_threshold_factor: float = Field(
        default=0.85, ge=0.5, le=1.0
    )
    admission_contextual_vad_peak_probability: float = Field(
        default=0.9, ge=0.5, le=1.0
    )
    # Device wake audio is privacy opt-in. This bound only limits a pending
    # detect -> binary Opus -> start sequence; it does not enable collection.
    wake_audio_pre_roll_max_ms: int = Field(default=2_000, ge=0, le=3_000)
    # Zero leaves utterance boundaries to VAD silence detection.
    max_utterance_seconds: float = Field(default=0.0, ge=0.0, le=60.0)
    max_utterance_buffer_bytes: int = Field(
        default=16 * 1_024 * 1_024,
        ge=1 * 1_024 * 1_024,
        le=64 * 1_024 * 1_024,
    )
    first_input_seconds: float = Field(default=180.0, gt=0.1, le=300.0)
    between_turns_seconds: float = Field(default=180.0, gt=0.1, le=600.0)
    closing_grace_seconds: float = Field(default=5.0, gt=0.1, le=60.0)
    # Zero means no absolute session ceiling; inactivity remains the only product close.
    max_session_seconds: float = Field(default=0.0, ge=0.0, le=3_600.0)
    asr_seconds: float = Field(default=8.0, gt=0.1, le=60.0)
    goodbye_text: str = "Tạm biệt, hẹn gặp lại."
    conversation_error_text: str = Field(
        default="Tôi chưa xử lý được câu vừa rồi, bạn nói lại giúp tôi nhé.",
        min_length=1,
        max_length=240,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
