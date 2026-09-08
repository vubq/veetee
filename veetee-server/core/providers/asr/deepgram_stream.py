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
        smart_format: bool = True,
        interim_results: bool = True,
        on_transcript_callback: Optional[Callable[..., Awaitable[None]]] = None,
        on_speech_started_callback: Optional[Callable[..., Awaitable[None]]] = None,
    ):
        self.api_key = api_key
        self.language = language
        self.model = model
        self.sample_rate = sample_rate
        self.endpointing_ms = endpointing_ms
        self.smart_format = bool(smart_format)
        self.interim_results = bool(interim_results)
        self.on_transcript_callback = on_transcript_callback
        self.on_speech_started_callback = on_speech_started_callback
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.is_running = False
        self.receive_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._utterance_has_text = False
        self._last_nonempty_transcript = ""
        self._idle_finalize_task: Optional[asyncio.Task] = None
        self._idle_finalize_delay = max(0.7, self.endpointing_ms / 1000 * 2.5)
        self._capture_generation = 0
        self._utterance_generation = 0
        self._drop_results_until_speech_started = False

    def _build_url(self) -> str:
        params = [
            f"model={self.model}",
            f"language={self.language}",
            f"smart_format={str(self.smart_format).lower()}",
            f"interim_results={str(self.interim_results).lower()}",
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

    def _cancel_idle_finalize(self):
        task = self._idle_finalize_task
        if task and not task.done():
            task.cancel()
        self._idle_finalize_task = None

    def _schedule_idle_finalize(self):
        self._cancel_idle_finalize()
        if not self._utterance_has_text or not self._last_nonempty_transcript:
            return
        self._idle_finalize_task = asyncio.create_task(self._finalize_after_idle())

    async def _finalize_after_idle(self):
        try:
            await asyncio.sleep(self._idle_finalize_delay)
            if not self._utterance_has_text or not self._last_nonempty_transcript:
                return
            transcript = self._last_nonempty_transcript
            logger.info(
                "Deepgram speech_final timeout after %.0f ms; finalizing: %r",
                self._idle_finalize_delay * 1000,
                transcript,
            )
            if self.on_transcript_callback:
                await self.on_transcript_callback(
                    transcript,
                    True,
                    True,
                    self._utterance_generation,
                )
            self._utterance_has_text = False
            self._last_nonempty_transcript = ""
        except asyncio.CancelledError:
            pass
        finally:
            if self._idle_finalize_task is asyncio.current_task():
                self._idle_finalize_task = None

    async def _receive_loop(self):
        try:
            while self.is_running and self.ws and not self.ws.closed:
                msg = await self.ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    
                    if msg_type == "SpeechStarted":
                        logger.debug("Deepgram VAD: SpeechStarted detected")
                        self._cancel_idle_finalize()
                        self._utterance_has_text = False
                        self._last_nonempty_transcript = ""
                        self._utterance_generation = self._capture_generation
                        self._drop_results_until_speech_started = False
                        if self.on_speech_started_callback:
                            await self.on_speech_started_callback(self._utterance_generation)
                    elif msg_type == "UtteranceEnd":
                        self._cancel_idle_finalize()
                        if self._utterance_has_text and self._last_nonempty_transcript:
                            transcript = self._last_nonempty_transcript
                            logger.info("Deepgram UtteranceEnd finalizing: %r", transcript)
                            if self.on_transcript_callback:
                                await self.on_transcript_callback(
                                    transcript,
                                    True,
                                    True,
                                    self._utterance_generation,
                                )
                        self._utterance_has_text = False
                        self._last_nonempty_transcript = ""
                    elif msg_type == "Results":
                        if self._drop_results_until_speech_started:
                            continue
                        channel = data.get("channel", {})
                        if isinstance(channel, dict):
                            alternatives = channel.get("alternatives", [])
                            if alternatives:
                                transcript = alternatives[0].get("transcript", "").strip()
                                is_final = data.get("is_final", False)
                                self._cancel_idle_finalize()
                                # Explicit Finalize flushes any buffered audio. Treat that
                                # response as an utterance boundary even if Deepgram doesn't
                                # also mark speech_final on the same payload.
                                speech_final = data.get("speech_final", False) or data.get("from_finalize", False)
                                if transcript:
                                    self._utterance_has_text = True
                                    self._last_nonempty_transcript = transcript

                                # Deepgram can emit speech_final=true with an empty
                                # transcript after useful interim/final text. Preserve the
                                # latest non-empty transcript so downstream still receives a
                                # real utterance boundary. Silence/noise-only boundaries stay
                                # suppressed.
                                emitted_transcript = transcript
                                emitted_is_final = is_final
                                if speech_final and not emitted_transcript and self._utterance_has_text:
                                    emitted_transcript = self._last_nonempty_transcript
                                    emitted_is_final = True

                                should_emit = bool(emitted_transcript)
                                if should_emit and self.on_transcript_callback:
                                    await self.on_transcript_callback(
                                        emitted_transcript,
                                        emitted_is_final,
                                        speech_final,
                                        self._utterance_generation,
                                    )
                                if speech_final:
                                    self._utterance_has_text = False
                                    self._last_nonempty_transcript = ""
                                elif is_final and transcript:
                                    # Some Deepgram streams finalize text segments but never
                                    # mark speech_final, especially with a continuously open
                                    # browser microphone. Finalize locally after a short period
                                    # without any newer transcript so the next sentence cannot
                                    # be merged into the previous one.
                                    self._schedule_idle_finalize()
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                    logger.debug(f"Deepgram WebSocket closed: {msg}")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Deepgram receive loop: {e}", exc_info=True)
        finally:
            self.is_running = False

    async def send_audio(self, pcm_bytes: bytes, capture_generation: Optional[int] = None):
        if capture_generation is not None:
            self._capture_generation = int(capture_generation)
        if not self.is_running or self.ws is None or self.ws.closed:
            await self.start()
        
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send_bytes(pcm_bytes)
            except Exception as e:
                logger.error(f"Failed to send audio to Deepgram: {e}")

    def invalidate_capture(self, capture_generation: int):
        self._capture_generation = int(capture_generation)
        self._utterance_generation = self._capture_generation
        self._drop_results_until_speech_started = True
        self._cancel_idle_finalize()
        self._utterance_has_text = False
        self._last_nonempty_transcript = ""

    async def finalize(self):
        """Force Deepgram to process buffered live audio without closing the socket."""
        self._cancel_idle_finalize()
        if self.ws and not self.ws.closed:
            try:
                await self.ws.send_str(json.dumps({"type": "Finalize"}))
            except Exception as e:
                logger.error(f"Failed to finalize Deepgram stream: {e}")

    async def stop(self):
        self.is_running = False
        self._cancel_idle_finalize()
        self._utterance_has_text = False
        self._last_nonempty_transcript = ""
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
