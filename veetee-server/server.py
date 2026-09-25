import asyncio
import copy
import os
import sys
import signal
import logging
from http import HTTPStatus
from core.access import WebSocketAccess, serve_session
from core.assistant_runtime import build_assistant_llm_view, build_assistant_tts_view
from core.management_store import ManagementStore
import websockets

from config.settings import (
    AppConfig,
    load_settings,
    validate_speech_segmentation_config,
)
from core.providers.asr.parakeet_silero import ParakeetSileroASR
from core.providers.tts.vieneu_local import VieneuLocalTTS
from core.providers.llm.groq_direct import build_engine_from_config
from core.providers.llm.speech_segments import SpeechSegmentationPolicy
from core.providers.llm.unavailable import UnavailableLLM
from core.response_audio_cache import ResponseAudioCache
from core.session import ClientSession
from core.turn_metrics import TurnTraceStore
from http_server import HttpServer, get_local_ip

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("VeeTeeServer")

_GROQ_TOKEN_LIMIT_PREFIX = "groq.token_limit."
_MAX_GROQ_TOKEN_LIMIT = 10**12

_RUNTIME_INT_SETTINGS = {
    "latency.target_first_audio_ms": ("latency", "target_first_audio_ms", 100, 5000),
    "latency.first_token_timeout_ms": ("latency", "first_token_timeout_ms", 100, 30000),
    "latency.total_turn_timeout_ms": ("latency", "total_turn_timeout_ms", 500, 120000),
    "asr.min_silence_duration_ms": ("asr", "min_silence_duration_ms", 96, 2000),
    "asr.speculative_start_silence_ms": (
        "asr", "speculative_start_silence_ms", 32, 1000
    ),
    "asr.min_speech_duration_ms": ("asr", "min_speech_duration_ms", 64, 2000),
    "asr.speech_start_frames": ("asr", "speech_start_frames", 1, 8),
    "asr.pre_speech_pad_ms": ("asr", "pre_speech_pad_ms", 0, 2000),
    "tts.send_ahead_ms": ("tts", "send_ahead_ms", 60, 1000),
    "tts.stream_queue_max_chunks": ("tts", "stream_queue_max_chunks", 1, 256),
    "tts.admission_timeout_ms": ("tts", "admission_timeout_ms", 100, 30000),
    "memory.lookup_timeout_ms": ("memory", "lookup_timeout_ms", 1, 5000),
    "conversation.history_turns": ("conversation", "history_turns", 1, 50),
    "tools.max_parallel_read_only": ("tools", "max_parallel_read_only", 1, 4),
    "tools.max_llm_rounds_per_turn": ("tools", "max_llm_rounds_per_turn", 2, 4),
    "llm.routing.max_attempts": ("llm", "routing.max_attempts", 1, 16),
    "llm.routing.discovery_max_inflight": (
        "llm", "routing.discovery_max_inflight", 1, 64
    ),
    "llm.speech_segmentation.min_segment_chars": (
        "llm", "speech_segmentation.min_segment_chars", 4, 500
    ),
    "llm.speech_segmentation.clause_target_chars": (
        "llm", "speech_segmentation.clause_target_chars", 16, 1000
    ),
    "llm.speech_segmentation.clause_min_chars": (
        "llm", "speech_segmentation.clause_min_chars", 8, 1000
    ),
    "llm.speech_segmentation.first_clause_min_chars": (
        "llm", "speech_segmentation.first_clause_min_chars", 4, 500
    ),
    "llm.speech_segmentation.first_clause_min_words": (
        "llm", "speech_segmentation.first_clause_min_words", 1, 20
    ),
    "llm.speech_segmentation.hard_max_segment_chars": (
        "llm", "speech_segmentation.hard_max_segment_chars", 32, 4000
    ),
    "llm.speech_segmentation.hard_cut_search_back": (
        "llm", "speech_segmentation.hard_cut_search_back", 1, 1000
    ),
    "llm.speech_segmentation.hard_cut_search_forward": (
        "llm", "speech_segmentation.hard_cut_search_forward", 1, 500
    ),
    "llm.speech_segmentation.first_segment_min_chars": (
        "llm", "speech_segmentation.first_segment_min_chars", 2, 200
    ),
    "llm.speech_segmentation.first_segment_min_words": (
        "llm", "speech_segmentation.first_segment_min_words", 1, 20
    ),
    "llm.speech_segmentation.first_soft_cut_chars": (
        "llm", "speech_segmentation.first_soft_cut_chars", 0, 200
    ),
    "llm.speech_segmentation.first_soft_cut_min_words": (
        "llm", "speech_segmentation.first_soft_cut_min_words", 1, 20
    ),
}

_RUNTIME_FLOAT_SETTINGS = {
    "asr.vad_threshold": ("asr", "vad_threshold", 0.05, 0.99),
    "asr.vad_threshold_low": ("asr", "vad_threshold_low", 0.01, 0.95),
    "asr.vad_end_threshold": ("asr", "vad_end_threshold", 0.01, 0.99),
    "asr.speculative_min_confidence": (
        "asr", "speculative_min_confidence", 0.0, 1.0
    ),
    "asr.speculative_llm_min_confidence": (
        "asr", "speculative_llm_min_confidence", 0.0, 1.0
    ),
    "tts.first_audio_priority_boost": (
        "tts", "first_audio_priority_boost", 0.0, 50.0
    ),
    "tts.scheduler_aging_per_second": (
        "tts", "scheduler_aging_per_second", 0.1, 20.0
    ),
    "llm.routing.headroom_pct": ("llm", "routing.headroom_pct", 0.0, 90.0),
    "llm.routing.admission_wait_ms": (
        "llm", "routing.admission_wait_ms", 0.0, 5000.0
    ),
    "llm.routing.discovery_wait_ms": (
        "llm", "routing.discovery_wait_ms", 0.0, 5000.0
    ),
    "llm.routing.inflight_penalty_s": (
        "llm", "routing.inflight_penalty_s", 0.0, 10.0
    ),
    "llm.routing.latency_ewma_alpha": (
        "llm", "routing.latency_ewma_alpha", 0.0, 1.0
    ),
    "llm.routing.latency_jitter_penalty": (
        "llm", "routing.latency_jitter_penalty", 0.0, 10.0
    ),
}

_RUNTIME_BOOL_SETTINGS = {
    "tts.speculative_prefetch_enabled": (
        "tts", "speculative_prefetch_enabled"
    ),
    "asr.speculative_inference_enabled": (
        "asr", "speculative_inference_enabled"
    ),
    "asr.speculative_llm_enabled": (
        "asr", "speculative_llm_enabled"
    ),
}


def _config_attr(config: AppConfig, section: str, attr_path: str):
    target = getattr(config, section)
    for part in attr_path.split("."):
        target = getattr(target, part)
    return target


def _set_config_attr(config: AppConfig, section: str, attr_path: str, value) -> None:
    target = getattr(config, section)
    parts = attr_path.split(".")
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)


def _runtime_bool_value(key: str, value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{key} must be a boolean")


def _runtime_int_value(key: str, value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    _section, _attr, minimum, maximum = _RUNTIME_INT_SETTINGS[key]
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _runtime_float_value(key: str, value) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc
    _section, _attr, minimum, maximum = _RUNTIME_FLOAT_SETTINGS[key]
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return parsed


def _groq_token_limit_value(value) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, bool):
        raise ValueError("Groq token limit must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Groq token limit must be an integer") from exc
    if parsed < 0 or parsed > _MAX_GROQ_TOKEN_LIMIT:
        raise ValueError(
            f"Groq token limit must be between 0 and {_MAX_GROQ_TOKEN_LIMIT}"
        )
    return parsed


class VeeTeeServer:
    def __init__(self, config: AppConfig):
        self.config = config
        self.local_ip = get_local_ip()
        server_dir = os.path.dirname(os.path.abspath(__file__))
        self.management_store = ManagementStore(os.path.join(server_dir, "data", "manager-state.json"))
        runtime = self.management_store.runtime_raw()
        file_segmentation = copy.deepcopy(config.llm.speech_segmentation)
        file_vad_threshold = float(config.asr.vad_threshold)
        file_vad_threshold_low = float(config.asr.vad_threshold_low)
        file_spec_enabled = bool(config.asr.speculative_inference_enabled)
        file_spec_start = int(config.asr.speculative_start_silence_ms)
        # Persisted manager values survive deploys. Secrets are materialized only
        # into this process and are never returned unmasked by the API.
        for key, value in runtime.items():
            if key.startswith("GROQ_API_KEY_") or key == "HF_TOKEN":
                os.environ[key] = str(value)
        if runtime.get("llm.model"):
            config.llm.model = str(runtime["llm.model"])
        if runtime.get("tts.voice"):
            config.tts.voice = str(runtime["tts.voice"])
        if runtime.get("asr.device"):
            config.asr.device = str(runtime["asr.device"])
        if runtime.get("server.barge_in_policy"):
            config.server.barge_in_policy = str(runtime["server.barge_in_policy"])
        for key, (section, attr, _minimum, _maximum) in _RUNTIME_INT_SETTINGS.items():
            if key not in runtime:
                continue
            try:
                _set_config_attr(
                    config,
                    section,
                    attr,
                    _runtime_int_value(key, runtime[key]),
                )
            except ValueError as exc:
                logger.warning("Ignoring invalid persisted runtime setting %s: %s", key, exc)
        for key, (section, attr, _minimum, _maximum) in _RUNTIME_FLOAT_SETTINGS.items():
            if key not in runtime:
                continue
            try:
                _set_config_attr(
                    config,
                    section,
                    attr,
                    _runtime_float_value(key, runtime[key]),
                )
            except ValueError as exc:
                logger.warning("Ignoring invalid persisted runtime setting %s: %s", key, exc)
        for key, (section, attr) in _RUNTIME_BOOL_SETTINGS.items():
            if key not in runtime:
                continue
            try:
                _set_config_attr(
                    config,
                    section,
                    attr,
                    _runtime_bool_value(key, runtime[key]),
                )
            except ValueError as exc:
                logger.warning("Ignoring invalid persisted runtime setting %s: %s", key, exc)
        if config.asr.vad_threshold_low >= config.asr.vad_threshold:
            logger.warning(
                "Persisted VAD thresholds are invalid; restoring config defaults"
            )
            config.asr.vad_threshold = file_vad_threshold
            config.asr.vad_threshold_low = file_vad_threshold_low
        if (
            config.asr.speculative_inference_enabled
            and config.asr.speculative_start_silence_ms
            >= config.asr.min_silence_duration_ms
        ):
            logger.warning(
                "Persisted speculative ASR settings are invalid; restoring config defaults"
            )
            config.asr.speculative_inference_enabled = file_spec_enabled
            config.asr.speculative_start_silence_ms = file_spec_start
        if config.latency.first_token_timeout_ms > config.latency.total_turn_timeout_ms:
            logger.warning(
                "Persisted first_token_timeout_ms exceeds total_turn_timeout_ms; "
                "using total turn timeout for first token as well"
            )
            config.latency.first_token_timeout_ms = config.latency.total_turn_timeout_ms
        try:
            validate_speech_segmentation_config(config.llm.speech_segmentation)
        except ValueError as exc:
            logger.warning(
                "Persisted speech segmentation policy is invalid; using config defaults: %s",
                exc,
            )
            config.llm.speech_segmentation = file_segmentation
        logger.info(f"Loaded config: LLM provider={config.llm.provider}, model={config.llm.model}, max_tokens={config.llm.max_tokens}")
        
        # 1. Initialize Vieneu Neural TTS (saved dashboard voice wins).
        self.tts_engine = VieneuLocalTTS(
            voice=config.tts.voice,
            voice_state_path=os.path.join(server_dir, "data", "voice.txt"),
            source_voice=config.tts.source_voice,
            sample_rate=config.tts.sample_rate,
            frame_duration_ms=config.tts.frame_duration_ms,
            stream_queue_max_chunks=config.tts.stream_queue_max_chunks,
            first_audio_priority_boost=config.tts.first_audio_priority_boost,
            scheduler_aging_per_second=config.tts.scheduler_aging_per_second,
            native_chunk_frames=config.tts.native_chunk_frames,
            denoise=config.tts.denoise,
            temperature=config.tts.temperature,
            admission_timeout_ms=config.tts.admission_timeout_ms,
            first_chunk_timeout_ms=config.tts.first_chunk_timeout_ms,
            stall_timeout_ms=config.tts.stall_timeout_ms,
        )
        
        # 2. Initialize LLM. Management/OTA must remain available even when
        # provider credentials are missing or malformed so an operator can
        # repair runtime configuration from the dashboard.
        try:
            self.llm_engine = build_engine_from_config(
                config.llm,
                server_dir=server_dir,
                usage_store=self.management_store,
            )
        except Exception as exc:
            logger.error("LLM initialization unavailable; starting management plane degraded: %s", exc)
            self.llm_engine = UnavailableLLM(
                model=config.llm.model,
                extra_models=config.llm.extra_models,
                reason=f"llm_configuration_unavailable: {type(exc).__name__}",
            )
        self.response_audio_cache = ResponseAudioCache(self.tts_engine, config.tts)
        self.recent_turn_store = TurnTraceStore(max_recent=100)
        self.runtime_readiness = {
            "llm_warm": False,
            "llm_error": getattr(self.llm_engine, "reason", ""),
            "llm_retryable": not bool(getattr(self.llm_engine, "permanent_unavailable", False)),
            "asr_ready": False,
            "asr_error": "",
            "error_fallback_ready": False,
            "error_fallback_provenance": "",
        }
        self._readiness_repair_task: asyncio.Task | None = None
        
        self.active_sessions = {}
        self.websocket_access = WebSocketAccess(config.server, config.management, self.management_store)
        self.http_server = HttpServer(
            self.config,
            self.active_sessions,
            tts_engine=self.tts_engine,
            llm_engine=self.llm_engine,
            response_audio_cache=self.response_audio_cache,
            recent_turn_store=self.recent_turn_store,
            runtime_readiness_ref=self.runtime_readiness,
            websocket_access=self.websocket_access,
            management_store=self.management_store,
            runtime_config_applier=self.apply_runtime_config,
        )

    @staticmethod
    def _runtime_secret_key(key: str) -> bool:
        return (
            key.startswith("GROQ_API_KEY_")
            or key == "HF_TOKEN"
        )

    def _assistant_for_session(self, session):
        device_id = str(getattr(session, "device_id", "") or "")
        client_id = str(getattr(session, "client_id", "") or "")
        if not device_id or device_id == "unknown":
            return None
        device = self.management_store.get_device(device_id, client_id or device_id)
        if not device or device.get("revoked"):
            return None
        assistant_id = str(device.get("assistant_id") or "")
        return self.management_store.get_assistant(assistant_id) if assistant_id else None

    async def apply_runtime_config(self, changes):
        """Validate and apply runtime config without restarting the service.

        Groq credentials/model changes are staged, probed against the real
        provider, and only persisted after a usable replacement engine exists.
        Existing sessions are hot-swapped to the new engine.
        """
        if not isinstance(changes, dict):
            raise ValueError("runtime changes must be an object")

        normalized = {str(key).strip(): value for key, value in changes.items()}
        for key in normalized:
            if not ManagementStore.runtime_key_allowed(key):
                raise ValueError(f"runtime setting is not allowed: {key}")
            if key.startswith(_GROQ_TOKEN_LIMIT_PREFIX):
                normalized[key] = _groq_token_limit_value(normalized[key])
        for key in set(normalized).intersection(_RUNTIME_INT_SETTINGS):
            if normalized[key] in (None, ""):
                continue
            normalized[key] = _runtime_int_value(key, normalized[key])
        for key in set(normalized).intersection(_RUNTIME_FLOAT_SETTINGS):
            if normalized[key] in (None, ""):
                continue
            normalized[key] = _runtime_float_value(key, normalized[key])
        for key in set(normalized).intersection(_RUNTIME_BOOL_SETTINGS):
            if normalized[key] in (None, ""):
                continue
            normalized[key] = _runtime_bool_value(key, normalized[key])

        staged_vad_high = float(
            normalized.get("asr.vad_threshold", self.config.asr.vad_threshold)
        )
        staged_vad_low = float(
            normalized.get("asr.vad_threshold_low", self.config.asr.vad_threshold_low)
        )
        if staged_vad_low >= staged_vad_high:
            raise ValueError("asr.vad_threshold_low must be < asr.vad_threshold")

        staged_spec_enabled = bool(
            normalized.get(
                "asr.speculative_inference_enabled",
                self.config.asr.speculative_inference_enabled,
            )
        )
        staged_spec_start = int(
            normalized.get(
                "asr.speculative_start_silence_ms",
                self.config.asr.speculative_start_silence_ms,
            )
        )
        staged_min_silence = int(
            normalized.get(
                "asr.min_silence_duration_ms",
                self.config.asr.min_silence_duration_ms,
            )
        )
        if staged_spec_enabled and staged_spec_start >= staged_min_silence:
            raise ValueError(
                "asr.speculative_start_silence_ms must be < "
                "asr.min_silence_duration_ms when speculative inference is enabled"
            )

        staged_target = int(
            normalized.get(
                "latency.target_first_audio_ms",
                self.config.latency.target_first_audio_ms,
            )
        )
        staged_first = int(
            normalized.get(
                "latency.first_token_timeout_ms",
                self.config.latency.first_token_timeout_ms,
            )
        )
        staged_total = int(
            normalized.get(
                "latency.total_turn_timeout_ms",
                self.config.latency.total_turn_timeout_ms,
            )
        )
        if staged_first < staged_target:
            raise ValueError(
                "latency.first_token_timeout_ms must be >= target_first_audio_ms"
            )
        if staged_total < staged_first:
            raise ValueError(
                "latency.total_turn_timeout_ms must be >= first_token_timeout_ms"
            )

        segmentation_keys = {
            key
            for key in normalized
            if key.startswith("llm.speech_segmentation.")
        }
        routing_keys = {
            key
            for key in normalized
            if key.startswith("llm.routing.")
        }
        previous_runtime = self.management_store.runtime_raw()
        previous_groq_state = self.management_store.groq_state_raw()
        runtime_persisted = False
        previous_int_values = {
            key: _config_attr(self.config, section, attr)
            for key, (section, attr, _minimum, _maximum) in _RUNTIME_INT_SETTINGS.items()
            if key in normalized
        }
        previous_float_values = {
            key: _config_attr(self.config, section, attr)
            for key, (section, attr, _minimum, _maximum) in _RUNTIME_FLOAT_SETTINGS.items()
            if key in normalized
        }
        previous_bool_values = {
            key: _config_attr(self.config, section, attr)
            for key, (section, attr) in _RUNTIME_BOOL_SETTINGS.items()
            if key in normalized
        }
        secret_keys = [key for key in normalized if self._runtime_secret_key(key)]
        previous_env = {key: os.environ.get(key) for key in secret_keys}
        previous_model = self.config.llm.model
        previous_segmentation = copy.deepcopy(self.config.llm.speech_segmentation)
        previous_asr_device = self.config.asr.device
        previous_barge_policy = self.config.server.barge_in_policy
        previous_voice = getattr(self.tts_engine, "voice", self.config.tts.voice)
        previous_tts_stream_queue_max_chunks = int(
            getattr(
                self.tts_engine,
                "stream_queue_max_chunks",
                self.config.tts.stream_queue_max_chunks,
            )
        )
        previous_tts_admission_timeout_ms = int(
            getattr(
                self.tts_engine,
                "admission_timeout_ms",
                self.config.tts.admission_timeout_ms,
            )
        )
        previous_tts_first_audio_priority_boost = float(
            getattr(
                self.tts_engine,
                "first_audio_priority_boost",
                self.config.tts.first_audio_priority_boost,
            )
        )
        previous_tts_scheduler_aging_per_second = float(
            getattr(
                self.tts_engine,
                "scheduler_aging_per_second",
                self.config.tts.scheduler_aging_per_second,
            )
        )

        llm_changed = (
            any(key.startswith("GROQ_API_KEY_") for key in normalized)
            or "llm.model" in normalized
        )
        asr_changed = (
            "asr.device" in normalized
            or "asr.min_silence_duration_ms" in normalized
            or "asr.speculative_inference_enabled" in normalized
            or "asr.speculative_start_silence_ms" in normalized
            or "asr.speculative_min_confidence" in normalized
            or "asr.min_speech_duration_ms" in normalized
            or "asr.speech_start_frames" in normalized
            or "asr.pre_speech_pad_ms" in normalized
            or "asr.vad_threshold" in normalized
            or "asr.vad_threshold_low" in normalized
            or "asr.vad_end_threshold" in normalized
        )
        new_llm = None
        llm_ready = self.runtime_readiness.get("llm_warm", False)

        try:
            for key in secret_keys:
                value = normalized[key]
                if value is None or value == "":
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value).strip()

            staged_llm = copy.deepcopy(self.config.llm)
            if "llm.model" in normalized and normalized["llm.model"]:
                staged_llm.model = str(normalized["llm.model"]).strip()
            for key in segmentation_keys:
                value = normalized.get(key)
                if value in (None, ""):
                    continue
                field_name = key.removeprefix("llm.speech_segmentation.")
                setattr(staged_llm.speech_segmentation, field_name, int(value))
            for key in routing_keys:
                value = normalized.get(key)
                if value in (None, ""):
                    continue
                field_name = key.removeprefix("llm.routing.")
                setattr(staged_llm.routing, field_name, value)
            validate_speech_segmentation_config(staged_llm.speech_segmentation)

            if llm_changed:
                has_groq_key = any(
                    name.startswith("GROQ_API_KEY_") and str(value or "").strip()
                    for name, value in os.environ.items()
                )
                if has_groq_key:
                    new_llm = build_engine_from_config(
                        staged_llm,
                        server_dir=os.path.dirname(os.path.abspath(__file__)),
                    )
                    if hasattr(new_llm, "set_model"):
                        new_llm.set_model(staged_llm.model, persist=False)
                    await new_llm.warmup()
                    llm_ready = True
                else:
                    new_llm = UnavailableLLM(
                        model=staged_llm.model,
                        extra_models=staged_llm.extra_models,
                        reason="llm_configuration_unavailable: no_groq_key",
                    )
                    llm_ready = False

            staged_asr_device = str(normalized.get("asr.device", self.config.asr.device) or "").strip()
            if "asr.device" in normalized:
                if staged_asr_device not in {"cuda", "cpu"}:
                    raise ValueError("asr.device must be cuda or cpu")
                await ParakeetSileroASR.preload(
                    self.config.asr.model,
                    staged_asr_device,
                )

            if "tts.voice" in normalized and normalized["tts.voice"]:
                requested_voice = str(normalized["tts.voice"]).strip()
                available = getattr(self.tts_engine, "available_voices", lambda: [])()
                names = {
                    str(item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item)
                    for item in available
                }
                if names and requested_voice not in names:
                    raise ValueError(f"unknown voice: {requested_voice!r}")

            values = self.management_store.update_runtime(normalized)
            runtime_persisted = True

            # The staged engine is deliberately not bound before warmup: a bad
            # credential must not reset/persist today's usage. Bind only after
            # the runtime transaction has committed successfully.
            if new_llm is not None:
                bind_usage = getattr(new_llm, "bind_usage_store", None)
                if callable(bind_usage):
                    bind_usage(self.management_store)
                values = self.management_store.runtime_public()

            if "llm.model" in normalized and normalized["llm.model"]:
                self.config.llm.model = str(normalized["llm.model"]).strip()
            if "asr.device" in normalized:
                self.config.asr.device = staged_asr_device
            if "server.barge_in_policy" in normalized and normalized["server.barge_in_policy"]:
                self.config.server.barge_in_policy = str(normalized["server.barge_in_policy"]).strip()
            if "tts.voice" in normalized and normalized["tts.voice"]:
                voice = str(normalized["tts.voice"]).strip()
                self.tts_engine.set_voice(voice, persist=False)
                self.config.tts.voice = voice

            for key, (section, attr, _minimum, _maximum) in _RUNTIME_INT_SETTINGS.items():
                if key in normalized and normalized[key] not in (None, ""):
                    _set_config_attr(
                        self.config,
                        section,
                        attr,
                        int(normalized[key]),
                    )
            for key, (section, attr, _minimum, _maximum) in _RUNTIME_FLOAT_SETTINGS.items():
                if key in normalized and normalized[key] not in (None, ""):
                    _set_config_attr(
                        self.config,
                        section,
                        attr,
                        float(normalized[key]),
                    )
            for key, (section, attr) in _RUNTIME_BOOL_SETTINGS.items():
                if key in normalized and normalized[key] not in (None, ""):
                    _set_config_attr(
                        self.config,
                        section,
                        attr,
                        bool(normalized[key]),
                    )

            if "tts.stream_queue_max_chunks" in normalized:
                self.tts_engine.stream_queue_max_chunks = int(
                    self.config.tts.stream_queue_max_chunks
                )
            if "tts.admission_timeout_ms" in normalized:
                self.tts_engine.admission_timeout_ms = int(
                    self.config.tts.admission_timeout_ms
                )
            if {
                "tts.first_audio_priority_boost",
                "tts.scheduler_aging_per_second",
            }.intersection(normalized):
                self.tts_engine.first_audio_priority_boost = float(
                    self.config.tts.first_audio_priority_boost
                )
                self.tts_engine.scheduler_aging_per_second = float(
                    self.config.tts.scheduler_aging_per_second
                )
                scheduler_policy_setter = getattr(
                    self.tts_engine, "set_scheduler_policy", None
                )
                if callable(scheduler_policy_setter):
                    scheduler_policy_setter(
                        first_audio_priority_boost=(
                            self.config.tts.first_audio_priority_boost
                        ),
                        scheduler_aging_per_second=(
                            self.config.tts.scheduler_aging_per_second
                        ),
                    )

            llm_reloaded = False
            session_updates = 0
            if routing_keys and new_llm is None:
                routing_setter = getattr(
                    self.llm_engine, "configure_routing", None
                )
                if callable(routing_setter):
                    routing = self.config.llm.routing
                    await routing_setter(
                        headroom_pct=routing.headroom_pct,
                        max_attempts=routing.max_attempts,
                        admission_wait_ms=routing.admission_wait_ms,
                        discovery_wait_ms=routing.discovery_wait_ms,
                        discovery_max_inflight=routing.discovery_max_inflight,
                        inflight_penalty_s=routing.inflight_penalty_s,
                        latency_ewma_alpha=routing.latency_ewma_alpha,
                        latency_jitter_penalty=routing.latency_jitter_penalty,
                    )

            if new_llm is not None:
                old_llm = self.llm_engine
                self.llm_engine = new_llm
                self.http_server.llm_engine = new_llm
                self.runtime_readiness["llm_warm"] = llm_ready
                self.runtime_readiness["llm_error"] = "" if llm_ready else getattr(new_llm, "reason", "")
                self.runtime_readiness["llm_retryable"] = llm_ready
                llm_reloaded = True

                for session in list(self.active_sessions.values()):
                    try:
                        assistant = self._assistant_for_session(session)
                        view = build_assistant_llm_view(new_llm, assistant, self.config)
                        await session.replace_llm_engine(view)
                        session_updates += 1
                    except Exception as exc:
                        logger.warning("Session LLM hot-swap failed session=%s: %s",
                                       getattr(session, "session_id", "?"), exc)

                close_old = getattr(old_llm, "close", None)
                if close_old is not None and old_llm is not new_llm:
                    try:
                        await close_old()
                    except Exception as exc:
                        logger.debug("Old LLM cleanup after hot reload failed: %s", exc)

            policy_keys = {
                "memory.lookup_timeout_ms",
                "conversation.history_turns",
                "tools.max_parallel_read_only",
            }
            if "server.barge_in_policy" in normalized or policy_keys.intersection(normalized):
                for session in list(self.active_sessions.values()):
                    touched = False
                    if "server.barge_in_policy" in normalized:
                        session.reload_session_policy()
                        touched = True
                    if "memory.lookup_timeout_ms" in normalized:
                        session.context_builder.lookup_timeout_ms = (
                            self.config.memory.lookup_timeout_ms
                        )
                        touched = True
                    if "conversation.history_turns" in normalized:
                        session.dialogue.max_history_turns = (
                            self.config.conversation.history_turns
                        )
                        session.dialogue._trim()
                        touched = True
                    if "tools.max_parallel_read_only" in normalized:
                        await session.tool_executor.reconfigure_read_parallelism(
                            self.config.tools.max_parallel_read_only
                        )
                        touched = True
                    if touched:
                        session_updates += 1

            asr_reloaded = False
            if asr_changed:
                failures = 0
                for session in list(self.active_sessions.values()):
                    try:
                        await session.reload_asr_engine()
                        session_updates += 1
                    except Exception as exc:
                        failures += 1
                        logger.warning("Session ASR hot-reload failed session=%s: %s",
                                       getattr(session, "session_id", "?"), exc)
                asr_reloaded = failures == 0
                self.runtime_readiness["asr_ready"] = failures == 0
                self.runtime_readiness["asr_error"] = "" if failures == 0 else f"{failures} session reload(s) failed"

            if segmentation_keys and new_llm is None:
                policy = SpeechSegmentationPolicy.from_config(self.config.llm)
                setter = getattr(self.llm_engine, "set_speech_segmentation", None)
                if callable(setter):
                    setter(policy)
                for session in list(self.active_sessions.values()):
                    try:
                        session_setter = getattr(
                            getattr(session, "llm_engine", None),
                            "set_speech_segmentation",
                            None,
                        )
                        if callable(session_setter):
                            session_setter(policy)
                            session_updates += 1
                    except Exception as exc:
                        logger.warning(
                            "Session speech segmentation hot-apply failed session=%s: %s",
                            getattr(session, "session_id", "?"),
                            exc,
                        )

            return {
                "values": values,
                "sessions_updated": session_updates,
                "llm_reloaded": llm_reloaded,
                "llm_ready": bool(self.runtime_readiness.get("llm_warm")),
                "asr_reloaded": asr_reloaded,
            }
        except Exception:
            for key, old_value in previous_env.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value
            if runtime_persisted:
                restore_runtime = {
                    key: previous_runtime[key] if key in previous_runtime else None
                    for key in normalized
                }
                try:
                    self.management_store.update_runtime(restore_runtime)
                    self.management_store.restore_groq_state(
                        previous_groq_state
                    )
                except Exception as restore_exc:
                    logger.error("Runtime persistence rollback failed: %s", restore_exc)
            for key, old_value in previous_int_values.items():
                section, attr, _minimum, _maximum = _RUNTIME_INT_SETTINGS[key]
                _set_config_attr(self.config, section, attr, old_value)
            for key, old_value in previous_float_values.items():
                section, attr, _minimum, _maximum = _RUNTIME_FLOAT_SETTINGS[key]
                _set_config_attr(self.config, section, attr, old_value)
            for key, old_value in previous_bool_values.items():
                section, attr = _RUNTIME_BOOL_SETTINGS[key]
                _set_config_attr(self.config, section, attr, old_value)
            self.config.llm.model = previous_model
            self.config.llm.speech_segmentation = previous_segmentation
            restore_segmentation = SpeechSegmentationPolicy.from_config(self.config.llm)
            if routing_keys:
                routing_setter = getattr(
                    self.llm_engine, "configure_routing", None
                )
                if callable(routing_setter):
                    try:
                        routing = self.config.llm.routing
                        await routing_setter(
                            headroom_pct=routing.headroom_pct,
                            max_attempts=routing.max_attempts,
                            admission_wait_ms=routing.admission_wait_ms,
                            discovery_wait_ms=routing.discovery_wait_ms,
                            discovery_max_inflight=routing.discovery_max_inflight,
                            inflight_penalty_s=routing.inflight_penalty_s,
                            latency_ewma_alpha=routing.latency_ewma_alpha,
                            latency_jitter_penalty=routing.latency_jitter_penalty,
                        )
                    except Exception:
                        pass
            restore_setter = getattr(self.llm_engine, "set_speech_segmentation", None)
            if callable(restore_setter):
                restore_setter(restore_segmentation)
            for session in list(self.active_sessions.values()):
                session_setter = getattr(
                    getattr(session, "llm_engine", None),
                    "set_speech_segmentation",
                    None,
                )
                if callable(session_setter):
                    try:
                        session_setter(restore_segmentation)
                    except Exception:
                        pass
            self.config.asr.device = previous_asr_device
            self.config.server.barge_in_policy = previous_barge_policy
            if getattr(self.tts_engine, "voice", previous_voice) != previous_voice:
                try:
                    self.tts_engine.set_voice(previous_voice, persist=False)
                except Exception:
                    pass
            self.tts_engine.stream_queue_max_chunks = (
                previous_tts_stream_queue_max_chunks
            )
            self.tts_engine.admission_timeout_ms = (
                previous_tts_admission_timeout_ms
            )
            self.tts_engine.first_audio_priority_boost = (
                previous_tts_first_audio_priority_boost
            )
            self.tts_engine.scheduler_aging_per_second = (
                previous_tts_scheduler_aging_per_second
            )
            scheduler_policy_setter = getattr(
                self.tts_engine, "set_scheduler_policy", None
            )
            if callable(scheduler_policy_setter):
                scheduler_policy_setter(
                    first_audio_priority_boost=(
                        previous_tts_first_audio_priority_boost
                    ),
                    scheduler_aging_per_second=(
                        previous_tts_scheduler_aging_per_second
                    ),
                )
            if new_llm is not None and new_llm is not self.llm_engine:
                close_new = getattr(new_llm, "close", None)
                if close_new is not None:
                    try:
                        await close_new()
                    except Exception:
                        pass
            raise

    def _session_engines(self, auth):
        assistant = (auth or {}).get("assistant") if isinstance(auth, dict) else None
        return (
            build_assistant_tts_view(self.tts_engine, assistant),
            build_assistant_llm_view(self.llm_engine, assistant, self.config),
            assistant,
        )

    async def handle_ws_connection(self, websocket: websockets.ServerConnection):
        access = self.websocket_access
        auth = access.authenticate(websocket.request.headers)
        if not access.origin_allowed(websocket.request.headers) or not auth:
            await websocket.close(code=1008, reason="unauthorized")
            return
        session_tts, session_llm, assistant = self._session_engines(auth)
        auth_device = auth.get("device") if isinstance(auth, dict) else None
        owner_id = self.config.memory.trusted_owner_id
        if isinstance(auth_device, dict) and str(auth_device.get("owner_id") or "").strip():
            owner_id = str(auth_device["owner_id"]).strip()
        def factory():
            return ClientSession(
                websocket=websocket,
                app_config=self.config,
                tts_engine=session_tts,
                llm_engine=session_llm,
                response_audio_cache=self.response_audio_cache if assistant is None else None,
                recovery_audio_cache=self.response_audio_cache,
                turn_trace_store=self.recent_turn_store,
                authenticated_owner_id=owner_id,
            )
        try:
            await serve_session(websocket, access, self.config, self.active_sessions, factory)
        except websockets.ConnectionClosed:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"Error in connection loop: {e}", exc_info=True)

    def authorize_ws(self, connection, request):
        if not self.websocket_access.origin_allowed(request.headers):
            return connection.respond(HTTPStatus.FORBIDDEN, "Origin denied\n")
        if not self.websocket_access.authenticated(request.headers):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Authentication required\n")
        if self.websocket_access.active >= self.config.server.ws_max_sessions:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Session limit\n")

    async def _prewarm_error_fallback(self) -> bool:
        """Prepare one AI-authored cached recovery clip used by failed turns.

        Runtime error handling is cached-only so a failed TTS engine is never
        called recursively while attempting to explain that same failure.
        """
        try:
            try:
                recovery = await self.response_audio_cache.get_recovery()
            except Exception:
                recovery = None
            if recovery is not None:
                provenance = recovery.provenance
                self.runtime_readiness["error_fallback_ready"] = True
                self.runtime_readiness["error_fallback_provenance"] = provenance
                return True

            pending = await self.response_audio_cache.get_pending_recovery()
            if pending is not None:
                cleaned, provenance = pending
            else:
                generator = getattr(self.llm_engine, "generate_recovery_message", None)
                if generator is None:
                    raise RuntimeError("LLM provider has no recovery-message generator")
                text = await asyncio.wait_for(
                    generator(),
                    timeout=max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0),
                )
                cleaned = str(text or "").strip()
                if not cleaned:
                    raise RuntimeError("LLM returned no recovery message")
                provenance = f"ai:{self.config.llm.model}"
            await self.response_audio_cache.prepare_recovery(
                cleaned,
                self.config.conversation.fixed_response_timeout_seconds,
                provenance=provenance,
            )
        except Exception as exc:
            self.runtime_readiness["error_fallback_ready"] = False
            self.runtime_readiness["error_fallback_provenance"] = ""
            logger.warning("AI recovery audio prewarm unavailable: %s", exc)
            return False
        self.runtime_readiness["error_fallback_ready"] = True
        self.runtime_readiness["error_fallback_provenance"] = provenance
        logger.info("AI recovery audio is ready provenance=%s", provenance)
        return True

    @staticmethod
    def _llm_error_retryable(exc: Exception) -> bool:
        text = str(exc or "").lower()
        permanent_markers = (
            "invalid api key",
            "invalid_api_key",
            "key pool is empty",
            "no enabled groq key",
            "authentication",
            "unauthorized",
            "model_not_found",
            "unknown model",
        )
        return not any(marker in text for marker in permanent_markers)

    async def _repair_readiness_assets(self):
        """Retry transient LLM warmup failures with bounded backoff."""
        if not self.runtime_readiness.get("llm_retryable", True):
            return
        delay = 1.0
        while True:
            try:
                if self.runtime_readiness.get("llm_warm"):
                    return
                if not self.runtime_readiness.get("llm_retryable", True):
                    return
                warmup = getattr(self.llm_engine, "warmup", None)
                if warmup is None:
                    self.runtime_readiness["llm_warm"] = True
                    return
                try:
                    await warmup()
                    self.runtime_readiness["llm_warm"] = True
                    return
                except Exception as exc:
                    self.runtime_readiness["llm_error"] = str(exc)
                    retryable = self._llm_error_retryable(exc)
                    self.runtime_readiness["llm_retryable"] = retryable
                    logger.warning("LLM warmup retry failed: %s", exc)
                    if not retryable:
                        return
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Readiness asset retry failed: %s", exc)
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2.0)

    async def start(self):
        # Warm the persistent LLM HTTP connection before accepting user turns.
        warmup = getattr(self.llm_engine, "warmup", None)
        if warmup is not None:
            try:
                await warmup()
                self.runtime_readiness["llm_warm"] = True
            except Exception as exc:
                self.runtime_readiness["llm_error"] = str(exc)
                self.runtime_readiness["llm_retryable"] = (
                    not bool(getattr(self.llm_engine, "permanent_unavailable", False))
                    and self._llm_error_retryable(exc)
                )
                logger.warning("LLM warmup failed; server will start degraded: %s", exc)
        else:
            self.runtime_readiness["llm_warm"] = True

        await self._prewarm_error_fallback()

        # Parakeet is a large local model. Load it before opening the web/WS
        # listeners so the first microphone connection cannot time out while
        # waiting for a cold model restore.
        logger.info("Preloading Parakeet ASR before accepting clients...")
        try:
            await ParakeetSileroASR.preload(
                self.config.asr.model,
                self.config.asr.device,
            )
            self.runtime_readiness["asr_ready"] = True
        except Exception as exc:
            self.runtime_readiness["asr_ready"] = False
            self.runtime_readiness["asr_error"] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "ASR preload unavailable; starting management plane degraded: %s",
                exc,
            )

        # 1. Start HTTP & OTA server
        await self.http_server.start()

        if not self.runtime_readiness.get("llm_warm"):
            self._readiness_repair_task = asyncio.create_task(
                self._repair_readiness_assets(),
                name="veetee-readiness-repair",
            )

        # 2. Start WebSocket server
        host = self.config.server.host
        ws_port = self.config.server.ws_port
        logger.info(f"WebSocket Server listening on ws://{self.local_ip}:{ws_port}")
        
        try:
            async with websockets.serve(
                self.handle_ws_connection,
                host,
                ws_port,
                ping_interval=30,
                process_request=self.authorize_ws,
                max_size=65536,
            ):
                logger.info("=" * 60)
                logger.info("  🚀 VeeTee Realtime Server is READY (Local Non-Docker)")
                logger.info(f"  • WebSocket URL: ws://{self.local_ip}:{ws_port}/")
                logger.info(f"  • OTA URL:       http://{self.local_ip}:{self.config.server.http_port}/ota/")
                logger.info(f"  • Web Dashboard: http://{self.local_ip}:{self.config.server.http_port}/")
                logger.info("=" * 60)
                
                stop_event = asyncio.Event()
                loop = asyncio.get_running_loop()
                registered_signals = []
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, stop_event.set)
                        registered_signals.append(sig)
                    except (NotImplementedError, RuntimeError, ValueError):
                        pass
                try:
                    await stop_event.wait()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass
                finally:
                    for sig in registered_signals:
                        try:
                            loop.remove_signal_handler(sig)
                        except (NotImplementedError, RuntimeError, ValueError):
                            pass
        finally:
            if self._readiness_repair_task is not None:
                self._readiness_repair_task.cancel()
                await asyncio.gather(self._readiness_repair_task, return_exceptions=True)
                self._readiness_repair_task = None

            # Close both standalone-WS and aiohttp-/ws sessions before
            # tearing down their listener. Session.close() is idempotent, so
            # connection-handler finalizers may race this safely.
            sessions = list(self.active_sessions.values())
            if sessions:
                await asyncio.gather(
                    *(session.close() for session in sessions),
                    return_exceptions=True,
                )
                self.active_sessions.clear()
            try:
                await self.http_server.stop()
            except Exception as exc:
                logger.warning("HTTP server cleanup failed: %s", exc)
            try:
                await self.response_audio_cache.shutdown()
            except Exception as exc:
                logger.warning("Response audio cache cleanup failed: %s", exc)
            shutdown_tts = getattr(self.tts_engine, "shutdown", None)
            if shutdown_tts is not None:
                try:
                    await shutdown_tts()
                except Exception as exc:
                    logger.warning("TTS cleanup failed: %s", exc)
            close_llm = getattr(self.llm_engine, "close", None)
            if close_llm is not None:
                try:
                    await close_llm()
                except Exception as exc:
                    logger.warning("LLM cleanup failed: %s", exc)

async def main():
    config = load_settings()
    log_level = getattr(logging, str(config.server.log_level).upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)
    server = VeeTeeServer(config)
    await server.start()

if __name__ == "__main__":
    asyncio.run(main())
