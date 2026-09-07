import asyncio
import aiohttp
import json
import logging
from typing import Callable, Optional, Awaitable
from core.providers.asr.base import BaseASR

logger = logging.getLogger("DeepgramASR")

class DeepgramStreamASR(BaseASR):
    def __init__(
        self,
        api_key: str,
        language: str = "vi",
        model: str = "nova-2",
        sample_rate: int = 16000,
        endpointing_ms: int = 250,
        on_transcript_callback: Optional[Callable[[str, bool, bool], Awaitable[None]]] = None,
        on_speech_started_callback: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self.api_key = api_key
        self.language = language
        self.model = model
        self.sample_rate = sample_rate
        self.endpointing_ms = endpointing_ms
        self.on_transcript_callback = on_transcript_callback
        self.on_speech_started_callback = on_speech_started_callback
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.is_running = False
        self.receive_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def _build_url(self) -> str:
        params = [
            f"model={self.model}",
            f"language={self.language}",
            "smart_format=true",
            "interim_results=true",
            f"endpointing={self.endpointing_ms}",
            "vad_events=true",
            "encoding=linear16",
            f"sample_rate={self.sample_rate}",
            "channels=1"
        ]
        return f"wss://api.deepgram.com/v1/listen?{'&'.join(params)}"

    async def start(self):
        async with self._lock:
            if self.is_running:
                return
            
            self.session = aiohttp.ClientSession()
            url = self._build_url()
            headers = {"Authorization": f"Token {self.api_key}"}
            
            try:
                self.ws = await self.session.ws_connect(url, headers=headers)
                self.is_running = True
                self.receive_task = asyncio.create_task(self._receive_loop())
                logger.info(f"Deepgram Live ASR connected ({self.model}, lang={self.language})")
            except Exception as e:
                logger.error(f"Failed to connect to Deepgram Live ASR: {e}")
                await self.stop()
                raise e

    async def _receive_loop(self):
        try:
            while self.is_running and self.ws and not self.ws.closed:
                msg = await self.ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    
                    if msg_type == "SpeechStarted":
                        logger.debug("Deepgram VAD: SpeechStarted detected")
                        if self.on_speech_started_callback:
                            await self.on_speech_started_callback()
                    elif msg_type == "Results":
                        channel = data.get("channel", {})
                        if isinstance(channel, dict):
                            alternatives = channel.get("alternatives", [])
                            if alternatives:
                                transcript = alternatives[0].get("transcript", "").strip()
                                is_final = data.get("is_final", False)
                                speech_final = data.get("speech_final", False)
                                if transcript and self.on_transcript_callback:
                                    await self.on_transcript_callback(transcript, is_final, speech_final)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                    logger.debug(f"Deepgram WebSocket closed: {msg}")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Deepgram receive loop: {e}", exc_info=True)
        finally:
            self.is_running = False

    async def send_audio(self, pcm_bytes: bytes):
        if not self.is_running or self.ws is None or self.ws.closed:
            await self.start()
        
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send_bytes(pcm_bytes)
            except Exception as e:
                logger.error(f"Failed to send audio to Deepgram: {e}")

    async def stop(self):
        self.is_running = False
        if self.receive_task:
            self.receive_task.cancel()
            self.receive_task = None
            
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send_str(json.dumps({"type": "CloseStream"}))
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        if self.session and not self.session.closed:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        logger.info("Deepgram Live ASR stopped")
