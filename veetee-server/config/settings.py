import os
import yaml
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_WAKE_WORDS = ["你好小智", "小爱同学", "小美同学", "VeeTee ơi"]
DEFAULT_EXIT_COMMANDS = ["tạm biệt", "kết thúc trò chuyện", "thoát trò chuyện"]

@dataclass
class ServerConfig:
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
    min_silence_duration_ms: int = 450
    min_speech_duration_ms: int = 160
    speech_start_frames: int = 2
    pre_speech_pad_ms: int = 512
    text_correction_enabled: bool = False
    text_correction_confidence_threshold: float = 0.78
    text_correction_timeout_ms: int = 900
    utterance_queue_max: int = 2
    max_utterance_ms: int = 30000

@dataclass
class LLMConfig:
    provider: str = "omniroute"
    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = "local-omniroute"
    model: str = "groq/qwen/qwen3.6-27b"
    temperature: float = 0.6
    max_tokens: int = 600
    reasoning_format: str = "hidden"
    base_prompt: str = "Bạn là VeeTee, một trợ lý ảo giọng nói tiếng Việt thông minh, thân thiện và hữu ích."
    prompt_template: str = "agent-base-prompt.txt"

@dataclass
class TTSConfig:
    provider: str = "vieneu"
    voice: str = "Xuân Vĩnh"
    source_voice: str = "Xuân Vĩnh"
    sample_rate: int = 24000
    frame_duration_ms: int = 60
    send_ahead_ms: int = 120
    stream_queue_max_chunks: int = 4
    denoise: bool = True
    temperature: float = 0.7


@dataclass
class ConversationConfig:
    enabled: bool = False
    wake_words: list[str] = field(default_factory=lambda: list(DEFAULT_WAKE_WORDS))
    greeting_enabled: bool = True
    greeting_text: str = ""
    greeting_ai_enabled: bool = True
    greeting_pool_size: int = 3
    audio_cache_enabled: bool = True
    idle_timeout_seconds: int = 120
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
    unified_turn_enabled: bool = True
    first_token_timeout_ms: int = 4000
    total_turn_timeout_ms: int = 15000
    context_lookup_timeout_ms: int = 10


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


@dataclass
class ToolsConfig:
    enabled: bool = True
    native_enabled: bool = True
    mcp_device_enabled: bool = False
    max_calls_per_turn: int = 3
    schema_limit: int = 16
    max_llm_rounds_per_turn: int = 1
    tool_result_synthesis: bool = False

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


def _validate_conversation_config(config: ConversationConfig) -> None:
    from core.conversation import normalize_command_text

    bool_fields = (
        "enabled",
        "greeting_enabled",
        "greeting_ai_enabled",
        "audio_cache_enabled",
        "goodbye_enabled",
        "goodbye_ai_enabled",
        "end_intent_ai_enabled",
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
        normalized = [normalize_command_text(item) for item in values]
        if any(not item for item in normalized):
            raise ValueError(f"conversation.{name} cannot contain empty aliases")
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"conversation.{name} contains duplicate aliases after normalization")

    for name in ("idle_timeout_seconds", "wake_start_wait_ms", "close_grace_ms", "greeting_pool_size", "ai_control_timeout_ms"):
        value = getattr(config, name)
        if type(value) is not int or value < 0:
            raise ValueError(f"conversation.{name} must be a non-negative integer")
    if config.greeting_pool_size < 1:
        raise ValueError("conversation.greeting_pool_size must be at least 1")
    if config.ai_control_timeout_ms < 100:
        raise ValueError("conversation.ai_control_timeout_ms must be at least 100")

    timeout = config.fixed_response_timeout_seconds
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("conversation.fixed_response_timeout_seconds must be greater than 0")
    config.fixed_response_timeout_seconds = float(timeout)

    wake = {normalize_command_text(item) for item in config.wake_words}
    exits = {normalize_command_text(item) for item in config.exit_commands}
    overlap = sorted(wake & exits)
    if overlap:
        raise ValueError(
            "conversation wake_words and exit_commands overlap after normalization: "
            + ", ".join(overlap)
        )


def _validate_app_config(config: AppConfig) -> None:
    for name in ("utterance_queue_max", "max_utterance_ms"):
        value = getattr(config.asr, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"asr.{name} must be a positive integer")
    if config.asr.max_utterance_ms < config.asr.min_speech_duration_ms:
        raise ValueError("asr.max_utterance_ms must be >= asr.min_speech_duration_ms")

    for name in ("unified_turn_enabled",):
        if type(getattr(config.latency, name)) is not bool:
            raise ValueError(f"latency.{name} must be a boolean")
    for name in ("first_token_timeout_ms", "total_turn_timeout_ms", "context_lookup_timeout_ms"):
        value = getattr(config.latency, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"latency.{name} must be a positive integer")

    for group_name, group, fields in (
        ("intent", config.intent, ("enabled", "semantic_end_enabled")),
        ("memory", config.memory, ("enabled", "durable_enabled")),
        ("tools", config.tools, ("enabled", "native_enabled", "mcp_device_enabled", "tool_result_synthesis")),
    ):
        for name in fields:
            if type(getattr(group, name)) is not bool:
                raise ValueError(f"{group_name}.{name} must be a boolean")

    for name in ("lookup_timeout_ms", "top_k", "max_memory_chars"):
        value = getattr(config.memory, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"memory.{name} must be a positive integer")
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

    for name in ("max_calls_per_turn", "schema_limit", "max_llm_rounds_per_turn"):
        value = getattr(config.tools, name)
        if type(value) is not int or value < 1:
            raise ValueError(f"tools.{name} must be a positive integer")
    if config.tools.max_calls_per_turn > 3:
        raise ValueError("tools.max_calls_per_turn must be <= 3")
    if config.tools.max_llm_rounds_per_turn not in {1, 2}:
        raise ValueError("tools.max_llm_rounds_per_turn must be 1 or 2")
    if config.tools.tool_result_synthesis and config.tools.max_llm_rounds_per_turn < 2:
        raise ValueError("tools.tool_result_synthesis requires max_llm_rounds_per_turn=2")
    if config.asr.text_correction_enabled and config.latency.unified_turn_enabled:
        raise ValueError("fast unified-turn profile requires asr.text_correction_enabled=false")

def load_settings(config_file: Optional[str] = None) -> AppConfig:
    if config_file is None:
        config_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
    
    if not os.path.exists(config_file):
        return AppConfig()
    
    with open(config_file, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    server_data = raw.get("server", {})
    asr_data = raw.get("asr", {})
    llm_data = raw.get("llm", {})
    tts_data = raw.get("tts", {})
    conversation_data = raw.get("conversation", {})
    latency_data = raw.get("latency", {})
    intent_data = raw.get("intent", {})
    memory_data = raw.get("memory", {})
    tools_data = raw.get("tools", {})

    if conversation_data is None:
        conversation_data = {}
    if not isinstance(conversation_data, dict):
        raise ValueError("conversation must be a mapping")

    conversation = ConversationConfig(
        **{k: v for k, v in conversation_data.items() if k in ConversationConfig.__annotations__}
    )
    _validate_conversation_config(conversation)

    for section_name, section_data in (
        ("latency", latency_data),
        ("intent", intent_data),
        ("memory", memory_data),
        ("tools", tools_data),
    ):
        if section_data is None:
            section_data = {}
        if not isinstance(section_data, dict):
            raise ValueError(f"{section_name} must be a mapping")
        if section_name == "latency": latency_data = section_data
        elif section_name == "intent": intent_data = section_data
        elif section_name == "memory": memory_data = section_data
        elif section_name == "tools": tools_data = section_data

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
    )
    _validate_app_config(config)
    return config
