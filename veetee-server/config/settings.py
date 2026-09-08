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
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)


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

    if conversation_data is None:
        conversation_data = {}
    if not isinstance(conversation_data, dict):
        raise ValueError("conversation must be a mapping")

    conversation = ConversationConfig(
        **{k: v for k, v in conversation_data.items() if k in ConversationConfig.__annotations__}
    )
    _validate_conversation_config(conversation)

    return AppConfig(
        server=ServerConfig(**{k: v for k, v in server_data.items() if k in ServerConfig.__annotations__}),
        asr=ASRConfig(**{k: v for k, v in asr_data.items() if k in ASRConfig.__annotations__}),
        llm=LLMConfig(**{k: v for k, v in llm_data.items() if k in LLMConfig.__annotations__}),
        tts=TTSConfig(**{k: v for k, v in tts_data.items() if k in TTSConfig.__annotations__}),
        conversation=conversation,
    )
