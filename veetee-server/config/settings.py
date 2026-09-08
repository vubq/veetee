import os
import yaml
from dataclasses import dataclass, field
from typing import Optional

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
    text_correction_enabled: bool = True
    text_correction_confidence_threshold: float = 0.78
    text_correction_timeout_ms: int = 900

@dataclass
class LLMConfig:
    provider: str = "omniroute"
    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = "local-omniroute"
    model: str = "qwen/qwen3.6-27b"
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
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)

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

    return AppConfig(
        server=ServerConfig(**{k: v for k, v in server_data.items() if k in ServerConfig.__annotations__}),
        asr=ASRConfig(**{k: v for k, v in asr_data.items() if k in ASRConfig.__annotations__}),
        llm=LLMConfig(**{k: v for k, v in llm_data.items() if k in LLMConfig.__annotations__}),
        tts=TTSConfig(**{k: v for k, v in tts_data.items() if k in TTSConfig.__annotations__}),
    )
