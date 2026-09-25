import os
import yaml
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_WAKE_WORDS = ["你好小智", "小爱同学", "小美同学", "VeeTee ơi"]
DEFAULT_EXIT_COMMANDS = ["tạm biệt", "kết thúc trò chuyện", "thoát trò chuyện"]

@dataclass
class ServerConfig:
    ws_allowed_origins: list[str] = field(default_factory=list)
    ws_max_sessions: int = 8
    ws_hello_timeout_seconds: int = 5
    timezone: str = DEFAULT_TIMEZONE
    host: str = "0.0.0.0"
    ws_port: int = 8000
    http_port: int = 8003
    log_level: str = "INFO"
    # Stock Xiaozhi firmware can explicitly interrupt with abort/listen:start.
    # Automatic speech-triggered barge-in remains disabled until a device AEC
    # profile has been verified independently.
    barge_in_policy: str = "client_only"

@dataclass
class ASRConfig:
    provider: str = "parakeet_silero"
    api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    model: str = "nvidia/parakeet-ctc-0.6b-vi"
    language: str = "vi"
    smart_format: bool = True
    interim_results: bool = True
    endpointing_ms: int = 250
    sample_rate: int = 16000
    device: str = "cuda"
    vad_model_path: str = "models/silero-vad/silero_vad.onnx"
    vad_threshold: float = 0.5
    vad_threshold_low: float = 0.3
    # Active-speech end threshold is independent from speech-start hysteresis.
    # Default 0.3 preserves the historical behavior; tune only with labelled audio.
    vad_end_threshold: float = 0.3
    min_silence_duration_ms: int = 192
    speculative_inference_enabled: bool = True
    speculative_start_silence_ms: int = 64
    speculative_min_confidence: float = 0.95
    # Start the same AI turn during trailing silence, but buffer all events
    # until ASR final confirms the exact transcript. No audio/tool/memory side
    # effect is allowed before commit.
    speculative_llm_enabled: bool = False
    speculative_llm_min_confidence: float = 0.95
    min_speech_duration_ms: int = 160
    speech_start_frames: int = 2
    pre_speech_pad_ms: int = 512
    text_correction_enabled: bool = False
    text_correction_confidence_threshold: float = 0.78
    text_correction_timeout_ms: int = 900
    utterance_queue_max: int = 2
    max_utterance_ms: int = 30000
    diagnostic_capture_enabled: bool = False
    diagnostic_capture_dir: str = "/tmp/veetee-asr-captures"
    diagnostic_capture_max_files: int = 20

@dataclass
class LLMKeyConfig:
    # One credential entry. Secrets live ONLY in env (api_key_env names the
    # variable); this file holds ids and quota-group mapping. Any suffix
    # works, so key count is flexible (A..D today, more later).
    id: str = ""
    api_key_env: str = ""
    quota_group: str = ""
    enabled: bool = True


@dataclass
class LLMRoutingConfig:
    headroom_pct: float = 10.0
    max_attempts: int = 2
    admission_wait_ms: float = 50.0
    discovery_wait_ms: float = 750.0
    discovery_max_inflight: int = 1
    inflight_penalty_s: float = 0.4
    latency_ewma_alpha: float = 0.3
    latency_jitter_penalty: float = 0.75


@dataclass
class SpeechSegmentationConfig:
    # Streaming LLM -> TTS policy. These are transport/prosody thresholds,
    # not semantic intent rules, and can be A/B tuned without code changes.
    min_segment_chars: int = 28
    clause_target_chars: int = 150
    clause_min_chars: int = 80
    first_clause_min_chars: int = 24
    first_clause_min_words: int = 4
    hard_max_segment_chars: int = 240
    hard_cut_search_back: int = 45
    hard_cut_search_forward: int = 40
    first_segment_min_chars: int = 8
    first_segment_min_words: int = 2
    first_soft_cut_chars: int = 0
    first_soft_cut_min_words: int = 3


QUOTA_DIMENSIONS = frozenset({"rpm", "rpd", "tpm", "tpd", "itpm", "otpm"})


@dataclass
class LLMConfig:
    provider: str = "groq"
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: str = "local-omniroute"
    model: str = "qwen/qwen3.6-27b"
    # Pool of Groq credentials. Empty pool = auto-scan GROQ_API_KEY_* env
    # (each alias gets its own quota group). Ignored by omniroute provider.
    key_pool: list = field(default_factory=list)
    # Quota caps per group, e.g. {"gA": {"rpm": 30, "tpm": 8000}}.
    # Omitted/empty groups run in discovery mode (bounded in-flight, caps
    # learned from response headers). Never fabricate another account's caps.
    quota_groups: dict = field(default_factory=dict)
    routing: LLMRoutingConfig = field(default_factory=LLMRoutingConfig)
    speech_segmentation: SpeechSegmentationConfig = field(
        default_factory=SpeechSegmentationConfig
    )
    # Reasoning effort for Qwen-style models ("none" suppresses <think>).
    # gpt-oss only accepts low/medium/high -> use model_reasoning_effort.
    reasoning_effort: str = "none"
    # Extra models for A/B tests (default model untouched). Per-model effort
    # override, e.g. {"openai/gpt-oss-20b": "low"}.
    extra_models: list = field(default_factory=list)
    model_reasoning_effort: dict = field(default_factory=dict)
    temperature: float = 0.6
    max_tokens: int = 600
    # Provider transport budgets. TurnRunner still owns the stricter
    # first-usable-event/whole-turn deadlines; these prevent socket/probe
    # requests from outliving the configured runtime budget.
    request_timeout_ms: int = 15000
    probe_timeout_ms: int = 8000
    control_timeout_ms: int = 2000
    # Keep Groq TCP/TLS connections warm across natural conversational pauses.
    http_keepalive_seconds: int = 120
    dns_cache_ttl_seconds: int = 300
    reasoning_format: str = "hidden"
    base_prompt: str = "Bạn là VeeTee, một trợ lý ảo giọng nói tiếng Việt thông minh, thân thiện và hữu ích."
    prompt_template: str = "agent-base-prompt.txt"
    # Persona budget replaces the old static 4000-char API/UI cap. Both byte
    # and estimated-token limits apply so a large persona cannot silently
    # overflow the model context. Runtime and management paths share this.
    base_prompt_max_bytes: int = 32 * 1024
    base_prompt_max_tokens: int = 8000

@dataclass
class TTSConfig:
    provider: str = "vieneu"
    voice: str = "Xuân Vĩnh"
    source_voice: str = "Xuân Vĩnh"
    sample_rate: int = 24000
    frame_duration_ms: int = 60
    send_ahead_ms: int = 120
    # Optional local-only speculative TTS. Audio is buffered and never sent
    # until the exact speculative transcript is confirmed by ASR final.
    speculative_prefetch_enabled: bool = False
    stream_queue_max_chunks: int = 4
    # Scheduler policy: first audio of a new turn may jump ahead of continuation
    # segments, while aging prevents continuation starvation under sustained load.
    first_audio_priority_boost: float = 5.0
    scheduler_aging_per_second: float = 2.0
    # Explicit low-TTFA VieNeu streamer profile instead of a machine literal.
    native_chunk_frames: int = 1
    denoise: bool = True
    temperature: float = 0.7
    # Split deadlines so a long playback is never cut by the generation
    # timeout. Generation/tool, first-chunk, stall and delivery budgets are
    # measured separately (M6.7).
    admission_timeout_ms: int = 5000
    first_chunk_timeout_ms: int = 1500
    stall_timeout_ms: int = 1500


@dataclass
class ConversationConfig:
    enabled: bool = False
    # Legacy/inert compatibility fields. They remain loadable so old configs
    # do not break, but runtime semantic routing never matches user text
    # against these lists or uses the literal greeting/goodbye values.
    wake_words: list[str] = field(default_factory=lambda: list(DEFAULT_WAKE_WORDS))
    greeting_enabled: bool = True
    greeting_text: str = ""
    greeting_ai_enabled: bool = True
    greeting_pool_size: int = 3
    audio_cache_enabled: bool = True
    idle_timeout_seconds: int = 120
    history_turns: int = 6
    # Long-history compression is AI-generated off the live turn. It is
    # cancelled when a new turn starts, so it never competes intentionally
    # with the latency-critical response path.
    history_summary_enabled: bool = True
    history_summary_high_water_turns: int = 5
    history_summary_min_new_turns: int = 3
    history_summary_max_chars: int = 1200
    history_summary_defer_ms: int = 250
    exit_commands: list[str] = field(default_factory=lambda: list(DEFAULT_EXIT_COMMANDS))
    goodbye_enabled: bool = True
    goodbye_text: str = ""
    goodbye_ai_enabled: bool = True
    end_intent_ai_enabled: bool = True
    ai_control_timeout_ms: int = 1800
    wake_start_wait_ms: int = 150
    fixed_response_timeout_seconds: float = 5.0
    close_grace_ms: int = 250


@dataclass
class LatencyConfig:
    # Compatibility switch for old configs. Runtime uses the unified path.
    unified_turn_enabled: bool = True
    # Warm-path objective; timeout remains looser so jitter does not turn a
    # 600 ms performance target into avoidable failed turns.
    target_first_audio_ms: int = 600
    first_token_timeout_ms: int = 1800
    total_turn_timeout_ms: int = 15000
    # Deprecated alias migrated to memory.lookup_timeout_ms when needed.
    context_lookup_timeout_ms: int = 10
    # Request budgeting uses a conservative character/token estimate so the
    # realtime path stays bounded without adding a tokenizer dependency.
    context_max_tokens: int = 8192
    context_chars_per_token: int = 4


@dataclass
class IntentConfig:
    enabled: bool = True
    semantic_end_enabled: bool = True
    confirmation_ttl_seconds: float = 15.0


@dataclass
class MemoryConfig:
    enabled: bool = True
    durable_enabled: bool = False
    database_path: str = "data/memory.sqlite3"
    # Durable personal memory is enabled only when the operator binds this
    # server instance to a trusted owner out-of-band. Device-Id/Client-Id are
    # never promoted to an authenticated owner automatically.
    trusted_owner_id: str = ""
    lookup_timeout_ms: int = 10
    top_k: int = 6
    max_memory_chars: int = 2400
    # Durable/session memory mutations sourced from voice require a minimum
    # acoustic confidence. AI still decides the semantics; this is only a
    # provenance/integrity gate against persisting uncertain ASR text.
    min_asr_confidence_for_write: float = 0.82


@dataclass
class ToolsConfig:
    enabled: bool = True
    native_enabled: bool = True
    mcp_device_enabled: bool = False
    max_calls_per_turn: int = 3
    schema_limit: int = 16
    # Normal chat remains one LLM inference. A real action receipt may use
    # bounded follow-up rounds so the model can chain A->B tools and then
    # phrase the actual receipts. The ceiling is finite and tested.
    max_llm_rounds_per_turn: int = 3
    tool_result_synthesis: bool = True
    max_parallel_read_only: int = 2
    # Deprecated compatibility flag. Tool receipts are always phrased by the
    # LLM; deterministic server code never renders user-facing tool speech.
    direct_read_only_speech_enabled: bool = False


@dataclass
class MusicConfig:
    # YouTube music search/play/control tools + background Opus streaming.
    # Needs network access to YouTube and the yt-dlp package. Device needs
    # no change: tracks stream as stock tts audio messages.
    enabled: bool = True
    search_results: int = 5
    resolve_timeout_s: float = 20.0
    stall_timeout_s: float = 12.0
    # Bounded client-side jitter buffer. Stock Xiaozhi keeps about 1.2s of
    # Opus packets; 360ms absorbs normal scheduler/network jitter while
    # keeping pause/stop responsive.
    send_ahead_ms: int = 360


@dataclass
class ManagementConfig:
    # Management endpoints stay disabled until the operator configures a
    # token. Stock OTA/WebSocket clients never need this credential.
    token: str = field(default_factory=lambda: os.getenv("VEETEE_MANAGEMENT_TOKEN", ""))
    test_voice_max_concurrency: int = 1
    test_voice_requests_per_minute: int = 12

@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    latency: LatencyConfig = field(default_factory=LatencyConfig)
    intent: IntentConfig = field(default_factory=IntentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)


def estimate_persona_tokens(text: str, chars_per_token: int = 4) -> int:
    """Conservative persona token estimate with a safety margin for Vietnamese/JSON."""
    chars_per_token = max(1, int(chars_per_token))
    chars = len((text or "").encode("utf-8", "ignore"))
    # Vietnamese diacritics and JSON schema overhead tokenize worse than 4
    # chars/token on many tokenizers, so keep a 25% safety margin and label
    # the result estimated wherever it is reported.
    return int(chars / chars_per_token * 1.25) + 1


def validate_base_prompt_budget(text: str, config: "AppConfig") -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("base_prompt must not be empty")
    raw_bytes = len(cleaned.encode("utf-8", "ignore"))
    if raw_bytes > config.llm.base_prompt_max_bytes:
        raise ValueError(
            f"base_prompt exceeds byte budget ({raw_bytes} > {config.llm.base_prompt_max_bytes})"
        )
    estimated = estimate_persona_tokens(
        cleaned, getattr(config.latency, "context_chars_per_token", 4)
    )
    if estimated > config.llm.base_prompt_max_tokens:
        raise ValueError(
            f"base_prompt exceeds token budget (est. {estimated} > {config.llm.base_prompt_max_tokens})"
        )


def _validate_base_prompt_budget(text: str, config: "AppConfig") -> None:
    # Startup validation shares the exact runtime/management budget check.
    # Over-budget personas fail loudly instead of being truncated silently.
    validate_base_prompt_budget(text, config)


def _validate_conversation_config(config: ConversationConfig) -> None:
    bool_fields = (
        "enabled",
        "greeting_enabled",
        "greeting_ai_enabled",
        "audio_cache_enabled",
        "goodbye_enabled",
        "goodbye_ai_enabled",
        "end_intent_ai_enabled",
        "history_summary_enabled",
    )
    for name in bool_fields:
        if type(getattr(config, name)) is not bool:
            raise ValueError(f"conversation.{name} must be a boolean")

    for name in ("greeting_text", "goodbye_text"):
        value = getattr(config, name)
        if not isinstance(value, str):
            raise ValueError(f"conversation.{name} must be a string")

    for name in ("wake_words", "exit_commands"):
        values = getattr(config, name)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"conversation.{name} must be a list of strings")
        if any(not item.strip() for item in values):
            raise ValueError(f"conversation.{name} cannot contain empty aliases")

    for name in (
        "idle_timeout_seconds",
        "history_turns",
        "history_summary_high_water_turns",
        "history_summary_min_new_turns",
        "history_summary_max_chars",
        "history_summary_defer_ms",
        "wake_start_wait_ms",
        "close_grace_ms",
        "greeting_pool_size",
        "ai_control_timeout_ms",
    ):
        value = getattr(config, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"conversation.{name} must be a non-negative integer")
    if config.greeting_pool_size < 1:
        raise ValueError("conversation.greeting_pool_size must be at least 1")
    if not 1 <= config.history_turns <= 50:
        raise ValueError("conversation.history_turns must be between 1 and 50")
    if not 2 <= config.history_summary_high_water_turns <= 50:
        raise ValueError("conversation.history_summary_high_water_turns must be between 2 and 50")
    if not 1 <= config.history_summary_min_new_turns <= 20:
        raise ValueError("conversation.history_summary_min_new_turns must be between 1 and 20")
    if not 256 <= config.history_summary_max_chars <= 8000:
        raise ValueError("conversation.history_summary_max_chars must be between 256 and 8000")
    if not 0 <= config.history_summary_defer_ms <= 5000:
        raise ValueError("conversation.history_summary_defer_ms must be between 0 and 5000")
    if config.ai_control_timeout_ms < 100:
        raise ValueError("conversation.ai_control_timeout_ms must be at least 100")

    timeout = config.fixed_response_timeout_seconds
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("conversation.fixed_response_timeout_seconds must be greater than 0")
    config.fixed_response_timeout_seconds = float(timeout)

    # Legacy wake/exit lists are retained as inert compatibility data. They
    # are not semantic routing tables and therefore do not need matcher-style
    # normalization/overlap rules.


def validate_speech_segmentation_config(config: SpeechSegmentationConfig) -> None:
    if not isinstance(config, SpeechSegmentationConfig):
        raise ValueError("llm.speech_segmentation must be a mapping")
    for name in (
        "min_segment_chars",
        "clause_target_chars",
        "clause_min_chars",
        "first_clause_min_chars",
        "first_clause_min_words",
        "hard_max_segment_chars",
        "hard_cut_search_back",
        "hard_cut_search_forward",
        "first_segment_min_chars",
        "first_segment_min_words",
        "first_soft_cut_min_words",
    ):
        value = getattr(config, name)
        if type(value) is not int or value < 1:
            raise ValueError(
                f"llm.speech_segmentation.{name} must be a positive integer"
            )
    if (
        type(config.first_soft_cut_chars) is not int
        or not 0 <= config.first_soft_cut_chars <= 200
    ):
        raise ValueError(
            "llm.speech_segmentation.first_soft_cut_chars must be an integer "
            "between 0 and 200"
        )
    if config.clause_min_chars > config.clause_target_chars:
        raise ValueError(
            "llm.speech_segmentation.clause_min_chars must be <= clause_target_chars"
        )
    if config.first_clause_min_chars > config.clause_target_chars:
        raise ValueError(
            "llm.speech_segmentation.first_clause_min_chars must be <= clause_target_chars"
        )
    if config.hard_max_segment_chars < config.clause_target_chars:
        raise ValueError(
            "llm.speech_segmentation.hard_max_segment_chars must be >= clause_target_chars"
        )
    if config.hard_cut_search_back >= config.hard_max_segment_chars:
        raise ValueError(
            "llm.speech_segmentation.hard_cut_search_back must be < hard_max_segment_chars"
        )


def _validate_app_config(config: AppConfig) -> None:
    for name in ("ws_max_sessions", "ws_hello_timeout_seconds"):
        if type(getattr(config.server, name)) is not int or getattr(config.server, name) < 1:
            raise ValueError(f"server.{name} must be a positive integer")
    if not isinstance(config.server.ws_allowed_origins, list) or any(
        not isinstance(value, str) or not value.startswith(("http://", "https://"))
        for value in config.server.ws_allowed_origins
    ):
        raise ValueError("server.ws_allowed_origins must be a list of HTTP origins")
    from zoneinfo import ZoneInfo
    ZoneInfo(config.server.timezone)
    for name in ("utterance_queue_max", "max_utterance_ms"):
        value = getattr(config.asr, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"asr.{name} must be a positive integer")
    if type(config.asr.min_silence_duration_ms) is not int or not 96 <= config.asr.min_silence_duration_ms <= 2000:
        raise ValueError("asr.min_silence_duration_ms must be an integer between 96 and 2000")
    if type(config.asr.speculative_inference_enabled) is not bool:
        raise ValueError("asr.speculative_inference_enabled must be a boolean")
    if (
        type(config.asr.speculative_start_silence_ms) is not int
        or not 32 <= config.asr.speculative_start_silence_ms <= 1000
    ):
        raise ValueError(
            "asr.speculative_start_silence_ms must be an integer between 32 and 1000"
        )
    if (
        isinstance(config.asr.speculative_min_confidence, bool)
        or not isinstance(config.asr.speculative_min_confidence, (int, float))
        or not 0 <= float(config.asr.speculative_min_confidence) <= 1
    ):
        raise ValueError(
            "asr.speculative_min_confidence must be a number between 0 and 1"
        )
    if type(config.asr.speculative_llm_enabled) is not bool:
        raise ValueError("asr.speculative_llm_enabled must be a boolean")
    if (
        isinstance(config.asr.speculative_llm_min_confidence, bool)
        or not isinstance(config.asr.speculative_llm_min_confidence, (int, float))
        or not 0 <= float(config.asr.speculative_llm_min_confidence) <= 1
    ):
        raise ValueError(
            "asr.speculative_llm_min_confidence must be a number between 0 and 1"
        )
    if (
        config.asr.speculative_inference_enabled
        and config.asr.speculative_start_silence_ms >= config.asr.min_silence_duration_ms
    ):
        raise ValueError(
            "asr.speculative_start_silence_ms must be < "
            "asr.min_silence_duration_ms when speculative inference is enabled"
        )
    if config.asr.speculative_llm_enabled and not config.asr.speculative_inference_enabled:
        raise ValueError(
            "asr.speculative_llm_enabled requires asr.speculative_inference_enabled"
        )
    if type(config.asr.min_speech_duration_ms) is not int or not 64 <= config.asr.min_speech_duration_ms <= 2000:
        raise ValueError("asr.min_speech_duration_ms must be an integer between 64 and 2000")
    if type(config.asr.speech_start_frames) is not int or not 1 <= config.asr.speech_start_frames <= 8:
        raise ValueError("asr.speech_start_frames must be an integer between 1 and 8")
    if type(config.asr.pre_speech_pad_ms) is not int or not 0 <= config.asr.pre_speech_pad_ms <= 2000:
        raise ValueError("asr.pre_speech_pad_ms must be an integer between 0 and 2000")
    for name in ("vad_threshold", "vad_threshold_low", "vad_end_threshold"):
        value = getattr(config.asr, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) < 1:
            raise ValueError(f"asr.{name} must be a number between 0 and 1")
    if config.asr.vad_threshold_low >= config.asr.vad_threshold:
        raise ValueError("asr.vad_threshold_low must be < asr.vad_threshold")
    if config.asr.max_utterance_ms < config.asr.min_speech_duration_ms:
        raise ValueError("asr.max_utterance_ms must be >= asr.min_speech_duration_ms")

    for name in ("unified_turn_enabled",):
        if type(getattr(config.latency, name)) is not bool:
            raise ValueError(f"latency.{name} must be a boolean")
    for name in (
        "target_first_audio_ms",
        "first_token_timeout_ms",
        "total_turn_timeout_ms",
        "context_lookup_timeout_ms",
        "context_max_tokens",
        "context_chars_per_token",
    ):
        value = getattr(config.latency, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"latency.{name} must be a positive integer")
    if config.latency.context_max_tokens <= config.llm.max_tokens:
        raise ValueError("latency.context_max_tokens must exceed llm.max_tokens")
    if (
        config.asr.provider.strip().lower()
        in {"parakeet_silero", "parakeet", "silero_parakeet"}
        and config.asr.sample_rate != 16000
    ):
        raise ValueError("Parakeet/Silero ASR requires asr.sample_rate=16000")

    for group_name, group, fields in (
        ("intent", config.intent, ("enabled", "semantic_end_enabled")),
        ("memory", config.memory, ("enabled", "durable_enabled")),
        ("tools", config.tools, ("enabled", "native_enabled", "mcp_device_enabled", "tool_result_synthesis")),
    ):
        for name in fields:
            if type(getattr(group, name)) is not bool:
                raise ValueError(f"{group_name}.{name} must be a boolean")

    if type(config.asr.endpointing_ms) is not int or not 100 <= config.asr.endpointing_ms <= 2000:
        raise ValueError("asr.endpointing_ms must be an integer between 100 and 2000")
    if type(config.tts.send_ahead_ms) is not int or not 60 <= config.tts.send_ahead_ms <= 1000:
        raise ValueError("tts.send_ahead_ms must be an integer between 60 and 1000")
    if type(config.tts.speculative_prefetch_enabled) is not bool:
        raise ValueError("tts.speculative_prefetch_enabled must be a boolean")

    for name in ("lookup_timeout_ms", "top_k", "max_memory_chars"):
        value = getattr(config.memory, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"memory.{name} must be a positive integer")
    confidence_gate = config.memory.min_asr_confidence_for_write
    if isinstance(confidence_gate, bool) or not isinstance(confidence_gate, (int, float)) or not 0.0 <= float(confidence_gate) <= 1.0:
        raise ValueError("memory.min_asr_confidence_for_write must be between 0 and 1")
    config.memory.min_asr_confidence_for_write = float(confidence_gate)
    if not isinstance(config.memory.database_path, str) or not config.memory.database_path.strip():
        raise ValueError("memory.database_path must be a non-empty string")
    if not isinstance(config.memory.trusted_owner_id, str):
        raise ValueError("memory.trusted_owner_id must be a string")
    if config.memory.durable_enabled and not config.memory.trusted_owner_id.strip():
        # Keep chat working safely: durable writes stay disabled rather than
        # treating self-declared client headers as an owner credential.
        config.memory.durable_enabled = False

    if not isinstance(config.intent.confirmation_ttl_seconds, (int, float)) or isinstance(
        config.intent.confirmation_ttl_seconds, bool
    ):
        raise ValueError("intent.confirmation_ttl_seconds must be a number")
    if not 1 <= float(config.intent.confirmation_ttl_seconds) <= 300:
        raise ValueError("intent.confirmation_ttl_seconds must be between 1 and 300")

    for name in ("max_calls_per_turn", "schema_limit", "max_llm_rounds_per_turn", "max_parallel_read_only"):
        value = getattr(config.tools, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"tools.{name} must be a positive integer")
    if not 1 <= config.tools.max_calls_per_turn <= 8:
        raise ValueError("tools.max_calls_per_turn must be between 1 and 8")
    if config.tools.max_llm_rounds_per_turn not in {1, 2, 3, 4}:
        raise ValueError("tools.max_llm_rounds_per_turn must be 1, 2, 3 or 4")
    if config.tools.tool_result_synthesis and config.tools.max_llm_rounds_per_turn < 2:
        raise ValueError("tools.tool_result_synthesis requires max_llm_rounds_per_turn>=2")
    if config.tools.schema_limit > 64:
        raise ValueError("tools.schema_limit must be <= 64")
    if not 1 <= config.tools.max_parallel_read_only <= 4:
        raise ValueError("tools.max_parallel_read_only must be between 1 and 4")
    if type(config.music.enabled) is not bool:
        raise ValueError("music.enabled must be a boolean")
    if type(config.music.search_results) is not int or not 1 <= config.music.search_results <= 10:
        raise ValueError("music.search_results must be an integer between 1 and 10")
    for name in ("resolve_timeout_s", "stall_timeout_s"):
        value = getattr(config.music, name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"music.{name} must be a positive number")
    if type(config.music.send_ahead_ms) is not int or not 60 <= config.music.send_ahead_ms <= 900:
        raise ValueError("music.send_ahead_ms must be an integer between 60 and 900")
    for name in ("base_prompt_max_bytes", "base_prompt_max_tokens"):
        value = getattr(config.llm, name)
        if type(value) is not int or value < 256:
            raise ValueError(f"llm.{name} must be an integer >= 256")
    for name in ("request_timeout_ms", "probe_timeout_ms", "control_timeout_ms"):
        value = getattr(config.llm, name)
        if type(value) is not int or value < 100:
            raise ValueError(f"llm.{name} must be an integer >= 100")
    if type(config.llm.http_keepalive_seconds) is not int or not 15 <= config.llm.http_keepalive_seconds <= 900:
        raise ValueError("llm.http_keepalive_seconds must be an integer between 15 and 900")
    if type(config.llm.dns_cache_ttl_seconds) is not int or not 15 <= config.llm.dns_cache_ttl_seconds <= 3600:
        raise ValueError("llm.dns_cache_ttl_seconds must be an integer between 15 and 3600")
    if config.llm.provider not in ("groq", "omniroute"):
        raise ValueError("llm.provider must be 'groq' or 'omniroute'")
    if not isinstance(config.llm.model, str) or not config.llm.model.strip():
        raise ValueError("llm.model must be a non-empty string")
    if not isinstance(config.llm.key_pool, list):
        raise ValueError("llm.key_pool must be a list")
    seen_ids: set = set()
    for entry in config.llm.key_pool:
        if not isinstance(entry, LLMKeyConfig):
            raise ValueError("llm.key_pool entries must be mappings")
        if not entry.id.strip():
            raise ValueError("llm.key_pool entry is missing id")
        if entry.id in seen_ids:
            raise ValueError(f"duplicate llm.key_pool id: {entry.id}")
        seen_ids.add(entry.id)
        if not isinstance(entry.api_key_env, str) or not entry.api_key_env.strip():
            raise ValueError(f"llm.key_pool {entry.id} is missing api_key_env")
        if not isinstance(entry.quota_group, str):
            raise ValueError(f"llm.key_pool {entry.id} has invalid quota_group")
        if type(entry.enabled) is not bool:
            raise ValueError(f"llm.key_pool {entry.id}.enabled must be a boolean")
    if not isinstance(config.llm.quota_groups, dict):
        raise ValueError("llm.quota_groups must be a mapping")
    for group, caps in config.llm.quota_groups.items():
        if not isinstance(caps, dict):
            raise ValueError(f"llm.quota_groups[{group}] must be a mapping")
        for dim, value in caps.items():
            if dim not in QUOTA_DIMENSIONS:
                raise ValueError(f"llm.quota_groups[{group}] has unknown dimension: {dim}")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"llm.quota_groups[{group}][{dim}] must be positive")
    routing = config.llm.routing
    if not isinstance(routing, LLMRoutingConfig):
        raise ValueError("llm.routing must be a mapping")
    if not 0.0 <= routing.headroom_pct <= 90.0:
        raise ValueError("llm.routing.headroom_pct must be between 0 and 90")
    if not 1 <= routing.max_attempts <= 16:
        raise ValueError("llm.routing.max_attempts must be between 1 and 16")
    if not 0.0 <= routing.admission_wait_ms <= 5000.0:
        raise ValueError("llm.routing.admission_wait_ms must be between 0 and 5000")
    if not 0.0 <= routing.discovery_wait_ms <= 5000.0:
        raise ValueError("llm.routing.discovery_wait_ms must be between 0 and 5000")
    if type(routing.discovery_max_inflight) is not int or routing.discovery_max_inflight < 1:
        raise ValueError("llm.routing.discovery_max_inflight must be a positive integer")
    if not 0.0 <= routing.inflight_penalty_s <= 10.0:
        raise ValueError("llm.routing.inflight_penalty_s must be between 0 and 10")
    if not 0.0 <= routing.latency_ewma_alpha <= 1.0:
        raise ValueError("llm.routing.latency_ewma_alpha must be between 0 and 1")
    if not 0.0 <= routing.latency_jitter_penalty <= 10.0:
        raise ValueError("llm.routing.latency_jitter_penalty must be between 0 and 10")
    validate_speech_segmentation_config(config.llm.speech_segmentation)
    if config.llm.reasoning_effort not in ("none", "low", "medium", "high"):
        raise ValueError("llm.reasoning_effort must be none/low/medium/high")
    if not isinstance(config.llm.extra_models, list) or any(
            not isinstance(m, str) or not m.strip() for m in config.llm.extra_models):
        raise ValueError("llm.extra_models must be a list of non-empty strings")
    if not isinstance(config.llm.model_reasoning_effort, dict):
        raise ValueError("llm.model_reasoning_effort must be a mapping")
    for model, effort in config.llm.model_reasoning_effort.items():
        if effort not in ("none", "low", "medium", "high"):
            raise ValueError(f"llm.model_reasoning_effort[{model}] must be none/low/medium/high")
    _validate_base_prompt_budget(config.llm.base_prompt, config)
    if config.asr.text_correction_enabled and config.latency.unified_turn_enabled:
        raise ValueError("fast unified-turn profile requires asr.text_correction_enabled=false")

    for name in ("admission_timeout_ms", "first_chunk_timeout_ms", "stall_timeout_ms"):
        value = getattr(config.tts, name)
        if type(value) is not int or value < 100:
            raise ValueError(f"tts.{name} must be an integer >= 100")
    if type(config.tts.native_chunk_frames) is not int or not 1 <= config.tts.native_chunk_frames <= 32:
        raise ValueError("tts.native_chunk_frames must be an integer between 1 and 32")
    if type(config.tts.stream_queue_max_chunks) is not int or not 1 <= config.tts.stream_queue_max_chunks <= 256:
        raise ValueError("tts.stream_queue_max_chunks must be an integer between 1 and 256")
    if not 0.0 <= float(config.tts.first_audio_priority_boost) <= 50.0:
        raise ValueError("tts.first_audio_priority_boost must be between 0 and 50")
    if not 0.1 <= float(config.tts.scheduler_aging_per_second) <= 20.0:
        raise ValueError("tts.scheduler_aging_per_second must be between 0.1 and 20")
    if not isinstance(config.management.token, str):
        raise ValueError("management.token must be a string")
    for name in ("test_voice_max_concurrency", "test_voice_requests_per_minute"):
        value = getattr(config.management, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"management.{name} must be a positive integer")

def load_settings(config_file: Optional[str] = None) -> AppConfig:
    if config_file is None:
        config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    
    if not os.path.exists(config_file):
        return AppConfig()
    
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot read VeeTee config file {config_file!r}; "
            "check file owner/mode for the service user"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Cannot open VeeTee config file {config_file!r}: {exc}"
        ) from exc

    server_data = raw.get("server", {})
    asr_data = raw.get("asr", {})
    llm_data = raw.get("llm", {})
    tts_data = raw.get("tts", {})
    conversation_data = raw.get("conversation", {})
    latency_data = raw.get("latency", {})
    intent_data = raw.get("intent", {})
    memory_data = raw.get("memory", {})
    tools_data = raw.get("tools", {})
    music_data = raw.get("music", {})
    management_data = raw.get("management", {})

    if conversation_data is None:
        conversation_data = {}
    if not isinstance(conversation_data, dict):
        raise ValueError("conversation must be a mapping")

    conversation = ConversationConfig(
        **{k: v for k, v in conversation_data.items() if k in ConversationConfig.__annotations__}
    )
    _validate_conversation_config(conversation)

    # Backward-compatible migration for the old latency-scoped lookup timeout.
    # The memory subsystem is the actual owner of this deadline.
    if (
        isinstance(latency_data, dict)
        and "context_lookup_timeout_ms" in latency_data
        and isinstance(memory_data, dict)
        and "lookup_timeout_ms" not in memory_data
    ):
        memory_data = dict(memory_data)
        memory_data["lookup_timeout_ms"] = latency_data["context_lookup_timeout_ms"]

    for section_name, section_data in (
        ("latency", latency_data),
        ("intent", intent_data),
        ("memory", memory_data),
        ("tools", tools_data),
        ("music", music_data),
        ("management", management_data),
    ):
        if section_data is None:
            section_data = {}
        if not isinstance(section_data, dict):
            raise ValueError(f"{section_name} must be a mapping")
        if section_name == "latency": latency_data = section_data
        elif section_name == "intent": intent_data = section_data
        elif section_name == "memory": memory_data = section_data
        elif section_name == "tools": tools_data = section_data
        elif section_name == "music": music_data = section_data
        elif section_name == "management": management_data = section_data

    # Empty string in YAML must not shadow env secrets. Fall back to env so
    # operators can leave api_key/token empty in local config.yaml.
    if not str(asr_data.get("api_key", "") or "").strip():
        asr_data = {k: v for k, v in asr_data.items() if k != "api_key"}
    if not str(management_data.get("token", "") or "").strip():
        management_data = {k: v for k, v in management_data.items() if k != "token"}

    pool_data = llm_data.get("key_pool", [])
    if pool_data is None:
        pool_data = []
    if not isinstance(pool_data, list):
        raise ValueError("llm.key_pool must be a list")
    key_pool = []
    for entry in pool_data:
        if not isinstance(entry, dict):
            raise ValueError("llm.key_pool entries must be mappings")
        known = {k: v for k, v in entry.items() if k in LLMKeyConfig.__annotations__}
        item = LLMKeyConfig(**known)
        if not item.quota_group.strip():
            item.quota_group = f"g{item.id}"
        key_pool.append(item)
    llm_data = dict(llm_data)
    llm_data["key_pool"] = key_pool
    routing_data = llm_data.get("routing", {})
    if routing_data is None:
        routing_data = {}
    if not isinstance(routing_data, dict):
        raise ValueError("llm.routing must be a mapping")
    llm_data["routing"] = LLMRoutingConfig(
        **{k: v for k, v in routing_data.items() if k in LLMRoutingConfig.__annotations__}
    )
    segmentation_data = llm_data.get("speech_segmentation", {})
    if segmentation_data is None:
        segmentation_data = {}
    if not isinstance(segmentation_data, dict):
        raise ValueError("llm.speech_segmentation must be a mapping")
    llm_data["speech_segmentation"] = SpeechSegmentationConfig(
        **{
            k: v
            for k, v in segmentation_data.items()
            if k in SpeechSegmentationConfig.__annotations__
        }
    )
    quota_groups = llm_data.get("quota_groups", {})
    if quota_groups is None:
        quota_groups = {}
    if not isinstance(quota_groups, dict):
        raise ValueError("llm.quota_groups must be a mapping")
    llm_data["quota_groups"] = quota_groups

    config = AppConfig(
        server=ServerConfig(**{k: v for k, v in server_data.items() if k in ServerConfig.__annotations__}),
        asr=ASRConfig(**{k: v for k, v in asr_data.items() if k in ASRConfig.__annotations__}),
        llm=LLMConfig(**{k: v for k, v in llm_data.items() if k in LLMConfig.__annotations__}),
        tts=TTSConfig(**{k: v for k, v in tts_data.items() if k in TTSConfig.__annotations__}),
        conversation=conversation,
        latency=LatencyConfig(**{k: v for k, v in latency_data.items() if k in LatencyConfig.__annotations__}),
        intent=IntentConfig(**{k: v for k, v in intent_data.items() if k in IntentConfig.__annotations__}),
        memory=MemoryConfig(**{k: v for k, v in memory_data.items() if k in MemoryConfig.__annotations__}),
        tools=ToolsConfig(**{k: v for k, v in tools_data.items() if k in ToolsConfig.__annotations__}),
        music=MusicConfig(**{k: v for k, v in music_data.items() if k in MusicConfig.__annotations__}),
        management=ManagementConfig(
            **{k: v for k, v in management_data.items() if k in ManagementConfig.__annotations__}
        ),
    )
    _validate_app_config(config)
    return config
