import asyncio
import json
import logging
import uuid
import time
from typing import Dict, Any, Optional
import websockets

from core.protocol import (
    ProtocolVersion,
    MessageType,
    unpack_audio_payload,
    pack_audio_payload,
    make_hello_response,
    make_stt_message,
    make_vad_message,
    make_llm_message,
    make_tts_message,
    parse_incoming_json,
)
from core.audio_utils import AudioCodec
from core.dialogue import DialogueContext
from core.providers.asr.base import BaseASR
from core.providers.asr.deepgram_stream import DeepgramStreamASR
from core.providers.llm.base import BaseLLM
from core.providers.tts.base import BaseTTS
from config.settings import AppConfig

logger = logging.getLogger("ClientSession")

class SessionState:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"

class ClientSession:
    def __init__(
        self,
        websocket: websockets.ServerConnection,
        app_config: AppConfig,
        tts_engine: BaseTTS,
        llm_engine: BaseLLM,
    ):
        self.websocket = websocket
        self.config = app_config
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        
        self.session_id = str(uuid.uuid4()).replace("-", "")
        self.version = ProtocolVersion.V1
        self.device_id = "unknown"
        self.client_id = "unknown"
        self.input_audio_format = "opus"
        
        self.state = SessionState.IDLE
        self.codec = AudioCodec(
            in_sample_rate=16000,
            out_sample_rate=app_config.tts.sample_rate,
            frame_duration_ms=app_config.tts.frame_duration_ms
        )
        
        self.dialogue = DialogueContext(max_history_turns=10)
        self.current_turn_task: Optional[asyncio.Task] = None
        self.current_cancel_event: Optional[asyncio.Event] = None
        self.is_active = True
        
        self.last_transcript = ""
        self.processed_transcript = ""
        self.final_transcript_parts = []
        
        # Instantiate streaming ASR for this session
        self.asr: BaseASR = DeepgramStreamASR(
            api_key=app_config.asr.api_key,
            language=app_config.asr.language,
            model=app_config.asr.model,
            sample_rate=app_config.asr.sample_rate,
            endpointing_ms=app_config.asr.endpointing_ms,
            on_transcript_callback=self._on_asr_transcript,
            on_speech_started_callback=self._on_speech_started,
        )

    async def initialize(self):
        if hasattr(self.websocket, "request") and self.websocket.request is not None:
            headers = dict(self.websocket.request.headers)
            self.device_id = headers.get("device-id", headers.get("Device-Id", "unknown"))
            self.client_id = headers.get("client-id", headers.get("Client-Id", "unknown"))
            proto_ver = headers.get("protocol-version", headers.get("Protocol-Version", "1"))
            try:
                self.version = int(proto_ver)
            except Exception:
                self.version = ProtocolVersion.V1
            
        logger.info(f"Client connected [Session: {self.session_id}, Device-Id: {self.device_id}, Version: {self.version}]")
        try:
            await self.asr.start()
        except Exception as e:
            logger.error(f"Failed to initialize ASR for session {self.session_id}: {e}")

    async def handle_message(self, message: Any):
        if isinstance(message, bytes):
            await self._handle_binary_audio(message)
        elif isinstance(message, str):
            await self._handle_text_json(message)

    async def _handle_binary_audio(self, data: bytes):
        if self.input_audio_format in ("pcm16", "linear16"):
            # Browser diagnostics send raw mono PCM16 at 16 kHz so the audio
            # still goes through the same Deepgram VAD/ASR pipeline as ESP32.
            await self.asr.send_audio(data)
            return

        opus_payload, timestamp = unpack_audio_payload(data, self.version)
        if not opus_payload:
            return
        
        pcm_bytes = self.codec.decode_opus_to_pcm16(opus_payload)
        if pcm_bytes:
            await self.asr.send_audio(pcm_bytes)

    async def _handle_text_json(self, text: str):
        data = parse_incoming_json(text)
        if not data:
            return
        
        msg_type = data.get("type")

        if msg_type == MessageType.HELLO:
            client_ver = data.get("version")
            if client_ver:
                self.version = int(client_ver)
            audio_params = data.get("audio_params") or {}
            requested_format = str(audio_params.get("format", "opus")).lower()
            if requested_format in ("opus", "pcm16", "linear16"):
                self.input_audio_format = requested_format
            
            hello_resp = make_hello_response(
                self.session_id,
                sample_rate=self.config.tts.sample_rate,
                frame_duration_ms=self.config.tts.frame_duration_ms
            )
            await self.send_text(hello_resp)
            logger.info(f"Handshake acknowledged for session {self.session_id} (Ver={self.version})")

        elif msg_type == MessageType.LISTEN:
            state = data.get("state")
            if state == "start":
                self.state = SessionState.LISTENING
                self._abort_turn()
                self.final_transcript_parts.clear()
            elif state == "detect":
                user_text = data.get("text", "").strip()
                logger.info(f"Listen detect received: '{user_text}'")
                self._abort_turn()
                if user_text:
                    await self._trigger_ai_turn(user_text)
                else:
                    self.state = SessionState.LISTENING
            elif state == "stop":
                self.state = SessionState.THINKING
                # The user explicitly ended capture (mic button / uploaded file).
                # Flush Deepgram's buffered streaming audio immediately.
                await self.asr.finalize()

        elif msg_type in ("text", "chat"):
            user_text = data.get("text", "").strip()
            if user_text:
                await self._trigger_ai_turn(user_text)

        elif msg_type == MessageType.ABORT:
            reason = data.get("reason", "")
            logger.info(f"Client requested abort (reason: {reason})")
            self._abort_turn()
            await self.send_text(make_tts_message(self.session_id, "stop"))

        elif msg_type == MessageType.PING:
            pass

    def _abort_turn(self):
        if self.current_cancel_event:
            self.current_cancel_event.set()
            self.current_cancel_event = None
        if self.current_turn_task and not self.current_turn_task.done():
            self.current_turn_task.cancel()
            self.current_turn_task = None

    async def _on_speech_started(self):
        await self.send_text(make_vad_message(self.session_id, "speech_started"))
        # Auto barge-in interruption when user speaks during playback
        if self.state == SessionState.SPEAKING:
            logger.info("Speech detected while speaking -> Barge-in aborting TTS playback")
            self._abort_turn()
            await self.send_text(make_tts_message(self.session_id, "stop"))
            self.state = SessionState.LISTENING

    async def _on_asr_transcript(self, transcript: str, is_final: bool, speech_final: bool):
        logger.info(f"ASR transcript received: '{transcript}' (final={is_final}, speech_final={speech_final})")
        self.last_transcript = transcript

        if is_final and transcript:
            if not self.final_transcript_parts or self.final_transcript_parts[-1] != transcript:
                self.final_transcript_parts.append(transcript)

        if is_final:
            display_text = " ".join(self.final_transcript_parts).strip()
        else:
            display_text = " ".join([*self.final_transcript_parts, transcript]).strip()
        
        # Stream live STT result to client screen
        await self.send_text(make_stt_message(
            self.session_id,
            display_text or transcript,
            is_final=is_final,
            speech_final=speech_final,
        ))
        
        if speech_final:
            await self.send_text(make_vad_message(self.session_id, "speech_ended"))
            final_text = " ".join(self.final_transcript_parts).strip() or transcript.strip()
            self.final_transcript_parts.clear()
            if final_text:
                await self._trigger_ai_turn(final_text)

    async def _trigger_ai_turn(self, transcript: str):
        if not transcript.strip() or transcript == self.processed_transcript:
            return
        
        self.processed_transcript = transcript
        logger.info(f"Triggering AI Turn for prompt: '{transcript}'")
        
        # Abort any prior playing turn
        self._abort_turn()
        
        # Launch AI streaming response pipeline with dedicated cancel event
        turn_cancel_event = asyncio.Event()
        self.current_cancel_event = turn_cancel_event
        self.current_turn_task = asyncio.create_task(
            self._process_ai_response(transcript, turn_cancel_event)
        )

    async def _process_ai_response(self, user_text: str, cancel_event: asyncio.Event):
        self.state = SessionState.THINKING
        t_start = time.time()
        
        self.dialogue.add_user_message(user_text)
        messages = self.dialogue.get_messages_for_llm()
        
        full_reply_clauses = []
        is_first_clause = True
        
        llm_stream = self.llm_engine.stream_chat(messages)
        try:
            async for clause, emotion in llm_stream:
                if cancel_event.is_set():
                    logger.info("Turn cancelled.")
                    return

                if is_first_clause:
                    ttft = time.time() - t_start
                    logger.info(f"LLM first clause ready in {ttft:.3f}s: '{clause}'")
                    
                    emo = emotion or "happy"
                    await self.send_text(make_llm_message(self.session_id, emo, "😊"))
                    await self.send_text(make_tts_message(self.session_id, "start"))
                    self.state = SessionState.SPEAKING
                    is_first_clause = False

                full_reply_clauses.append(clause)
                await self.send_text(make_tts_message(self.session_id, "sentence_start", clause))
                
                # Stream TTS Opus frames for this clause
                first_audio = True
                async for opus_frame in self.tts_engine.stream_sentence_to_opus(clause, cancel_event):
                    if cancel_event.is_set():
                        break
                    
                    if first_audio:
                        ttfa = time.time() - t_start
                        logger.info(f"🚀 Time-to-first-audio (TTFA): {ttfa:.3f}s")
                        first_audio = False
                    
                    packet = pack_audio_payload(opus_frame, self.version)
                    await self.send_binary(packet)
                    await asyncio.sleep(0.005)

                if cancel_event.is_set():
                    break

            if not cancel_event.is_set() and not is_first_clause:
                await self.send_text(make_tts_message(self.session_id, "stop"))
                self.state = SessionState.IDLE
                
                complete_text = " ".join(full_reply_clauses)
                self.dialogue.add_assistant_message(complete_text)
                logger.info(f"Completed turn in {time.time() - t_start:.3f}s: '{complete_text}'")

        except asyncio.CancelledError:
            logger.info("Response task cancelled")
        except Exception as e:
            logger.error(f"Error during AI response processing: {e}", exc_info=True)
            await self.send_text(make_tts_message(self.session_id, "stop"))
            self.state = SessionState.IDLE
        finally:
            aclose = getattr(llm_stream, "aclose", None)
            if aclose is not None:
                await aclose()

    async def send_text(self, text: str):
        if not self.is_active:
            return
        try:
            await self.websocket.send(text)
        except Exception as e:
            logger.debug(f"Error sending text: {e}")

    async def send_binary(self, data: bytes):
        if not self.is_active:
            return
        try:
            await self.websocket.send(data)
        except Exception as e:
            logger.debug(f"Error sending binary: {e}")

    async def close(self):
        self.is_active = False
        self._abort_turn()
        await self.asr.stop()
        logger.info(f"Session {self.session_id} closed")
