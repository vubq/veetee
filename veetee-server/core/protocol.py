import struct
import json
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger("VeeTeeProtocol")

class ProtocolVersion:
    V1 = 1
    V2 = 2
    V3 = 3

class MessageType:
    HELLO = "hello"
    LISTEN = "listen"
    ABORT = "abort"
    STT = "stt"
    LLM = "llm"
    TTS = "tts"
    MCP = "mcp"
    SYSTEM = "system"
    ALERT = "alert"
    PING = "ping"

def unpack_audio_payload(data: bytes, version: int = 1) -> Tuple[bytes, int]:
    """
    Unpacks binary audio frame from client according to protocol version.
    Returns (raw_opus_bytes, timestamp_ms).
    """
    if version == 2 and len(data) >= 16:
        # >HHIII : version (2), type (2), reserved (4), timestamp (4), payload_size (4)
        ver, msg_type, reserved, timestamp, payload_size = struct.unpack(">HHIII", data[:16])
        if payload_size > len(data) - 16:
            logger.warning("Dropping truncated protocol v2 audio packet")
            return b"", timestamp
        payload = data[16:16 + payload_size]
        return payload, timestamp
    elif version == 3 and len(data) >= 4:
        # >BBH : type (1), reserved (1), payload_size (2)
        msg_type, reserved, payload_size = struct.unpack(">BBH", data[:4])
        if payload_size > len(data) - 4:
            logger.warning("Dropping truncated protocol v3 audio packet")
            return b"", 0
        payload = data[4:4 + payload_size]
        return payload, 0
    elif version in (2, 3):
        logger.warning("Dropping undersized protocol v%s audio packet", version)
        return b"", 0
    else:
        # Version 1: raw opus frame
        return data, 0

def pack_audio_payload(opus_bytes: bytes, version: int = 1, timestamp: int = 0) -> bytes:
    """
    Packs opus frame for client according to protocol version.
    """
    if version == 2:
        header = struct.pack(">HHIII", 2, 0, 0, timestamp, len(opus_bytes))
        return header + opus_bytes
    elif version == 3:
        header = struct.pack(">BBH", 0, 0, len(opus_bytes))
        return header + opus_bytes
    else:
        return opus_bytes

def make_hello_response(session_id: str, sample_rate: int = 24000, frame_duration_ms: int = 60) -> str:
    return json.dumps({
        "type": "hello",
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {
            "format": "opus",
            "sample_rate": sample_rate,
            "channels": 1,
            "frame_duration": frame_duration_ms
        }
    })

def make_stt_message(
    session_id: str,
    text: str,
    is_final: Optional[bool] = None,
    speech_final: Optional[bool] = None,
) -> str:
    msg: Dict[str, Any] = {
        "session_id": session_id,
        "type": "stt",
        "text": text
    }
    if is_final is not None:
        msg["is_final"] = is_final
    if speech_final is not None:
        msg["speech_final"] = speech_final
    return json.dumps(msg)

def make_vad_message(session_id: str, state: str) -> str:
    return json.dumps({
        "session_id": session_id,
        "type": "vad",
        "state": state,
    })

def make_llm_message(session_id: str, emotion: str, text: str) -> str:
    return json.dumps({
        "session_id": session_id,
        "type": "llm",
        "emotion": emotion,
        "text": text
    })

def make_tts_message(
    session_id: str,
    state: str,
    text: Optional[str] = None,
) -> str:
    msg: Dict[str, Any] = {
        "session_id": session_id,
        "type": "tts",
        "state": state
    }
    if text is not None:
        msg["text"] = text
    return json.dumps(msg)

def parse_incoming_json(data: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(data)
    except Exception as e:
        logger.warning(f"Failed to parse JSON: {data} ({e})")
        return None
