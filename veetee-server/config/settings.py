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

@dataclass
class ASRConfig:
    provider: str = "deepgram"
    api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", ""))
    model: str = "nova-2"
    language: str = "vi"
    smart_format: bool = True
    interim_results: bool = True
    endpointing_ms: int = 250
    sample_rate: int = 16000

@dataclass
class LLMConfig:
    provider: str = "omniroute"
    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = "local-omniroute"
    model: str = "qwen/qwen3.6-27b"
    temperature: float = 0.6
    max_tokens: int = 600
    reasoning_format: str = "hidden"
    system_prompt: str = "Bạn là trợ lý ảo giọng nói tiếng Việt thông minh. Luôn trả lời trực tiếp trong 1-2 câu ngắn gọn, không giải thích dài dòng, không dùng markdown. Bắt đầu câu trả lời bằng một cảm xúc như [happy] hoặc [neutral]."

@dataclass
class TTSConfig:
    provider: str = "vieneu"
    voice: str = "Xuân Vĩnh"
    sample_rate: int = 24000
    frame_duration_ms: int = 60
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
