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
from core.audio_pacing import AudioPacer
from core.dialogue import DialogueContext
from core.providers.asr.base import BaseASR
from core.providers.asr.deepgram_stream import DeepgramStreamASR
from core.providers.asr.parakeet_silero import ParakeetSileroASR
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
        self._turn_generation = 0
        self._capture_generation = 0
        self._playback_guard_until = 0.0
        
        self.last_transcript = ""
        self.processed_transcript = ""
        self.final_transcript_parts = []
        self._speech_active = False
        self._discard_asr_until_speech_final = False
        # Xiaozhi keeps voice processing enabled while speaking only in
        # realtime mode. Auto/manual therefore retain the echo guard below.
        self.listening_mode = "auto"
        self.server_side_aec_requested = False
        self.barge_in_policy = str(self.config.server.barge_in_policy or "client_only").strip().lower()
        if self.barge_in_policy != "client_only":
            logger.warning(
                "Unsupported barge_in_policy=%r; falling back to client_only",
                self.barge_in_policy,
            )
            self.barge_in_policy = "client_only"
        
        self.asr: BaseASR = self._create_asr()

    def _create_asr(self) -> BaseASR:
        provider = self.config.asr.provider.strip().lower()
        if provider in {"parakeet_silero", "parakeet", "silero_parakeet"}:
            return ParakeetSileroASR(
                model=self.config.asr.model,
                sample_rate=self.config.asr.sample_rate,
                device=self.config.asr.device,
                vad_model_path=self.config.asr.vad_model_path,
                vad_threshold=self.config.asr.vad_threshold,
                vad_threshold_low=self.config.asr.vad_threshold_low,
                min_silence_duration_ms=self.config.asr.min_silence_duration_ms,
                min_speech_duration_ms=self.config.asr.min_speech_duration_ms,
                speech_start_frames=self.config.asr.speech_start_frames,
                pre_speech_pad_ms=self.config.asr.pre_speech_pad_ms,
                on_transcript_callback=self._on_asr_transcript,
                on_speech_started_callback=self._on_speech_started,
            )
        if provider == "deepgram":
            return DeepgramStreamASR(
                api_key=self.config.asr.api_key,
                language=self.config.asr.language,
                model=self.config.asr.model,
                sample_rate=self.config.asr.sample_rate,
                endpointing_ms=self.config.asr.endpointing_ms,
                smart_format=self.config.asr.smart_format,
                interim_results=self.config.asr.interim_results,
                on_transcript_callback=self._on_asr_transcript,
                on_speech_started_callback=self._on_speech_started,
            )
        raise ValueError(f"Unsupported ASR provider: {self.config.asr.provider}")

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
            # Browser diagnostics and ESP32 both feed the same PCM16 ASR path.
            await self.asr.send_audio(data, self._capture_generation)
            return

        opus_payload, timestamp = unpack_audio_payload(data, self.version)
        if not opus_payload:
            return
        
        pcm_bytes = self.codec.decode_opus_to_pcm16(opus_payload)
        if pcm_bytes:
            await self.asr.send_audio(pcm_bytes, self._capture_generation)

    async def _handle_text_json(self, text: str):
        data = parse_incoming_json(text)
        if not data:
            return
        
        msg_type = data.get("type")

        if msg_type == MessageType.HELLO:
            client_ver = data.get("version")
            if client_ver:
                self.version = int(client_ver)
            features = data.get("features") or {}
            self.server_side_aec_requested = features.get("aec") is True
            audio_params = data.get("audio_params") or {}
            requested_format = str(audio_params.get("format", "opus")).lower()
            if requested_format in ("opus", "pcm16", "linear16"):
                self.input_audio_format = requested_format
            input_frame_duration = audio_params.get("frame_duration", 60)
            if self.input_audio_format == "opus" and not self.codec.configure_input_frame_duration(input_frame_duration):
                logger.warning(
                    "Unsupported client Opus frame_duration=%r; keeping %d ms input decoder setting",
                    input_frame_duration,
                    self.codec.in_frame_duration_ms,
                )
            
            hello_resp = make_hello_response(
                self.session_id,
                sample_rate=self.config.tts.sample_rate,
                frame_duration_ms=self.config.tts.frame_duration_ms
            )
            await self.send_text(hello_resp)
            logger.info(
                "Handshake acknowledged for session %s (Ver=%s, mode=%s, reported_server_aec=%s, barge_in_policy=%s, input_frame=%sms)",
                self.session_id,
                self.version,
                self.listening_mode,
                self.server_side_aec_requested,
                self.barge_in_policy,
                self.codec.in_frame_duration_ms,
            )

        elif msg_type == MessageType.LISTEN:
            state = data.get("state")
            if state == "start":
                was_speaking = self.state == SessionState.SPEAKING
                requested_mode = str(data.get("mode", "")).strip().lower()
                if requested_mode in {"realtime", "auto", "manual"}:
                    self.listening_mode = requested_mode
                self._abort_turn()
                self._invalidate_capture()
                self._playback_guard_until = 0.0
                if was_speaking:
                    # Reference FW accepts tts:stop and moves from speaking to
                    # listening (except manual-stop mode, where it owns the
                    # subsequent state transition itself). Explicit listening
                    # start interrupts any buffered playback.
                    await self.send_text(make_tts_message(self.session_id, "stop"))
                self.state = SessionState.LISTENING
                self.final_transcript_parts.clear()
                self._speech_active = False
                self._discard_asr_until_speech_final = False
                # A repeated utterance is still a new user turn. De-duplication
                # should only protect one capture, not suppress the same phrase
                # spoken again later.
                self.processed_transcript = ""
            elif state == "detect":
                user_text = data.get("text", "").strip()
                logger.info(f"Listen detect received: '{user_text}'")
                self._abort_turn()
                self._invalidate_capture()
                if user_text:
                    await self._trigger_ai_turn(user_text)
                else:
                    self.state = SessionState.LISTENING
            elif state == "stop":
                self.state = SessionState.THINKING
                # The user explicitly ended capture. Flush the current local
                # utterance immediately instead of waiting for VAD silence.
                await self.asr.finalize()

        elif msg_type in ("text", "chat"):
            user_text = data.get("text", "").strip()
            if user_text:
                await self._trigger_ai_turn(user_text)

        elif msg_type == MessageType.ABORT:
            reason = data.get("reason", "")
            logger.info(f"Client requested abort (reason: {reason})")
            self._abort_turn()
            self._invalidate_capture()
            self._playback_guard_until = 0.0
            await self.send_text(make_tts_message(self.session_id, "stop"))
            self.state = (
                SessionState.IDLE
                if self.listening_mode == "manual"
                else SessionState.LISTENING
            )
            self.final_transcript_parts.clear()
            self._speech_active = False
            self._discard_asr_until_speech_final = False
            self.processed_transcript = ""

        elif msg_type == "browser_audio_debug":
            logger.info(
                "Browser audio debug [Session: %s] reason=%s recording=%s frames=%s "
                "last_frame_age_ms=%s context=%s/%sHz track=%s muted=%s enabled=%s tts_sources=%s",
                self.session_id,
                data.get("reason"),
                data.get("recording"),
                data.get("frame_count"),
                data.get("last_frame_age_ms"),
                data.get("mic_context_state"),
                data.get("mic_context_rate"),
                data.get("track_state"),
                data.get("track_muted"),
                data.get("track_enabled"),
                data.get("tts_sources"),
            )

        elif msg_type == MessageType.PING:
            pass

    def _abort_turn(self):
        self._turn_generation += 1
        if self.current_cancel_event:
            self.current_cancel_event.set()
            self.current_cancel_event = None
        if self.current_turn_task and not self.current_turn_task.done():
            self.current_turn_task.cancel()
            self.current_turn_task = None

    def _invalidate_capture(self):
        self._capture_generation += 1
        invalidate = getattr(self.asr, "invalidate_capture", None)
        if invalidate is not None:
            invalidate(self._capture_generation)

    def _owns_turn(self, turn_generation: int) -> bool:
        return self.is_active and self._turn_generation == turn_generation

    async def _on_speech_started(self, capture_generation: Optional[int] = None):
        if capture_generation is not None and capture_generation != self._capture_generation:
            logger.info(
                "Discarding stale ASR speech-start generation=%s current=%s",
                capture_generation,
                self._capture_generation,
            )
            return

        if self.state == SessionState.SPEAKING:
            # Stock FW has no runtime proof that device-side AEC is effective.
            # Under client_only policy, speech detected while TTS is playing is
            # treated as possible echo. Explicit abort/listen:start from the FW
            # remains the supported interruption path.
            self._discard_asr_until_speech_final = True
            self._speech_active = False
            self.final_transcript_parts.clear()
            if self.listening_mode == "realtime":
                logger.info(
                    "Ignoring realtime ASR during TTS under client_only barge-in policy"
                )
            else:
                logger.info("Ignoring ASR speech while TTS is playing (echo guard)")
            return

        if (
            self.listening_mode == "realtime"
            and self.barge_in_policy == "client_only"
            and time.monotonic() < self._playback_guard_until
        ):
            self._discard_asr_until_speech_final = True
            self._speech_active = False
            self.final_transcript_parts.clear()
            logger.info("Ignoring ASR during estimated playback tail under client_only policy")
            return

        self._speech_active = True
        await self.send_text(make_vad_message(self.session_id, "speech_started"))

    async def _on_asr_transcript(
        self,
        transcript: str,
        is_final: bool,
        speech_final: bool,
        capture_generation: Optional[int] = None,
    ):
        if capture_generation is not None and capture_generation != self._capture_generation:
            logger.info(
                "Discarding stale ASR transcript generation=%s current=%s",
                capture_generation,
                self._capture_generation,
            )
            return
        transcript = (transcript or "").strip()

        # CTC can occasionally emit only punctuation/unknown-token glyphs for
        # unusable audio (for example "⁇") with misleadingly high confidence.
        # Treat those exactly like an empty ASR result so they never start an
        # LLM/TTS turn.
        if transcript and not any(char.isalnum() for char in transcript):
            logger.info("Discarding non-lexical ASR transcript: %r", transcript)
            transcript = ""

        if self._discard_asr_until_speech_final:
            logger.info(
                f"Discarding ASR during TTS: '{transcript}' "
                f"(final={is_final}, speech_final={speech_final})"
            )
            if speech_final:
                self._discard_asr_until_speech_final = False
                self.final_transcript_parts.clear()
                self._speech_active = False
            return
        logger.info(f"ASR transcript received: '{transcript}' (final={is_final}, speech_final={speech_final})")
        self.last_transcript = transcript

        if is_final and transcript:
            if not self.final_transcript_parts or self.final_transcript_parts[-1] != transcript:
                self.final_transcript_parts.append(transcript)

        if is_final:
            display_text = " ".join(self.final_transcript_parts).strip()
        else:
            display_text = " ".join([*self.final_transcript_parts, transcript]).strip()
        
        # Keep partial/intermediate results immediate. A speech-final Parakeet
        # result may first pass through the confidence-gated correction below.
        if display_text and not speech_final:
            await self.send_text(make_stt_message(
                self.session_id,
                display_text,
                is_final=is_final,
                speech_final=speech_final,
            ))
        
        if speech_final:
            final_stage_started = time.perf_counter()
            final_text = " ".join(self.final_transcript_parts).strip() or transcript.strip()
            self.final_transcript_parts.clear()
            final_text = await self._maybe_correct_asr_transcript(final_text)
            logger.info(
                "ASR final stage completed in %.0f ms after speech-final callback (correction included when enabled)",
                (time.perf_counter() - final_stage_started) * 1000,
            )
            if final_text:
                self.last_transcript = final_text
                await self.send_text(make_stt_message(
                    self.session_id,
                    final_text,
                    is_final=True,
                    speech_final=True,
                ))
            if self._speech_active or final_text:
                await self.send_text(make_vad_message(self.session_id, "speech_ended"))
            self._speech_active = False
            if final_text:
                await self._trigger_ai_turn(final_text)

    async def _maybe_correct_asr_transcript(self, transcript: str) -> str:
        original = (transcript or "").strip()
        if not original or not self.config.asr.text_correction_enabled:
            return original

        confidence = getattr(self.asr, "last_word_confidence", None)
        if confidence is None or confidence >= self.config.asr.text_correction_confidence_threshold:
            return original

        corrector = getattr(self.llm_engine, "correct_transcript", None)
        if corrector is None:
            return original

        started = time.perf_counter()
        timeout_seconds = max(0.1, self.config.asr.text_correction_timeout_ms / 1000.0)
        try:
            corrected = await asyncio.wait_for(corrector(original), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("ASR correction timed out after %.0f ms", timeout_seconds * 1000)
            return original
        except Exception as exc:
            logger.warning("ASR correction failed: %s", exc)
            return original

        corrected = (corrected or "").strip() or original
        logger.info(
            "ASR correction (confidence=%.3f, %.0f ms): %r -> %r",
            confidence,
            (time.perf_counter() - started) * 1000,
            original,
            corrected,
        )
        return corrected

    async def _trigger_ai_turn(self, transcript: str):
        if not transcript.strip() or transcript == self.processed_transcript:
            return
        
        self.processed_transcript = transcript
        logger.info(f"Triggering AI Turn for prompt: '{transcript}'")
        
        # Abort any prior playing turn
        self._abort_turn()
        
        # Launch AI streaming response pipeline with dedicated cancel event
        turn_cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = turn_cancel_event
        self.current_turn_task = asyncio.create_task(
            self._process_ai_response(transcript, turn_cancel_event, turn_generation)
        )

    async def _process_ai_response(
        self,
        user_text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
    ):
        if not self._owns_turn(turn_generation):
            return
        self.state = SessionState.THINKING
        t_start = time.perf_counter()
        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )
        
        self.dialogue.add_user_message(user_text)
        messages = self.dialogue.get_messages_for_llm()
        
        full_reply_clauses = []
        is_first_clause = True
        first_binary_sent = False
        clause_queue: asyncio.Queue = asyncio.Queue(maxsize=3)
        queue_done = object()
        producer_errors = []
        llm_stream = self.llm_engine.stream_chat(messages)

        async def produce_clauses():
            try:
                async for clause, emotion in llm_stream:
                    if cancel_event.is_set():
                        return
                    await clause_queue.put((clause, emotion))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                producer_errors.append(exc)
            finally:
                # Producer shutdown must never wait for queue capacity after
                # the consumer has been cancelled.
                if not cancel_event.is_set():
                    try:
                        clause_queue.put_nowait(queue_done)
                    except asyncio.QueueFull:
                        pass

        producer_task = asyncio.create_task(produce_clauses())
        try:
            while True:
                if producer_task.done() and clause_queue.empty():
                    if producer_errors:
                        raise producer_errors[0]
                    break
                queued_item = await clause_queue.get()
                if queued_item is queue_done:
                    if producer_errors:
                        raise producer_errors[0]
                    break

                clause, emotion = queued_item
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    logger.info("Turn cancelled.")
                    return

                if is_first_clause:
                    post_asr_first_clause = time.perf_counter() - t_start
                    logger.info(
                        "Post-ASR first LLM clause ready in %.3fs: %r",
                        post_asr_first_clause,
                        clause,
                    )
                    
                    emo = emotion or "happy"
                    if not self._owns_turn(turn_generation):
                        return
                    await self.send_text(make_llm_message(self.session_id, emo, "😊"))
                    await self.send_text(make_tts_message(self.session_id, "start"))
                    self.state = SessionState.SPEAKING
                    is_first_clause = False

                full_reply_clauses.append(clause)
                await self.send_text(make_tts_message(self.session_id, "sentence_start", clause))
                
                # Stream TTS Opus frames for this clause
                async for opus_frame in self.tts_engine.stream_sentence_to_opus(clause, cancel_event):
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        break

                    if not await pacer.wait_for_send(cancel_event):
                        break
                    
                    packet = pack_audio_payload(opus_frame, self.version)
                    if not self._owns_turn(turn_generation):
                        break
                    await self.send_binary(packet)
                    if not first_binary_sent:
                        logger.info(
                            "Post-ASR first TTS binary sent in %.3fs",
                            time.perf_counter() - t_start,
                        )
                        first_binary_sent = True
                    pacer.record_frame_sent()
                    if pacer.playback_end is not None:
                        self._playback_guard_until = max(
                            self._playback_guard_until,
                            pacer.playback_end,
                        )

                if cancel_event.is_set():
                    break

            if (
                not cancel_event.is_set()
                and self._owns_turn(turn_generation)
                and not is_first_clause
            ):
                await self.send_text(make_tts_message(self.session_id, "stop"))
                logger.info(
                    "Post-ASR tts:stop sent in %.3fs",
                    time.perf_counter() - t_start,
                )
                self.state = (
                    SessionState.IDLE
                    if self.listening_mode == "manual"
                    else SessionState.LISTENING
                )
                # With realtime input, keep the echo guard until the
                # current VAD utterance reaches speech_final. Clearing it at
                # tts:stop can let the speaker tail become a new user turn.
                if not (
                    self.listening_mode == "realtime"
                    and self._discard_asr_until_speech_final
                ):
                    self._discard_asr_until_speech_final = False
                self._speech_active = False
                self.final_transcript_parts.clear()
                
                complete_text = " ".join(full_reply_clauses)
                self.dialogue.add_assistant_message(complete_text)
                logger.info(
                    "Completed post-ASR response pipeline in %.3fs: %r",
                    time.perf_counter() - t_start,
                    complete_text,
                )
                logger.info(
                    "Audio pacing: sent=%.0fms max_lead=%.0fms wait=%.0fms tail=%.0fms",
                    pacer.total_audio_ms,
                    pacer.max_estimated_lead_ms,
                    pacer.total_wait_ms,
                    pacer.estimated_lead_ms(),
                )
            elif (
                not cancel_event.is_set()
                and self._owns_turn(turn_generation)
                and is_first_clause
            ):
                self.state = (
                    SessionState.IDLE
                    if self.listening_mode == "manual"
                    else SessionState.LISTENING
                )

        except asyncio.CancelledError:
            logger.info("Response task cancelled")
        except Exception as e:
            logger.error(f"Error during AI response processing: {e}", exc_info=True)
            if self._owns_turn(turn_generation):
                await self.send_text(make_tts_message(self.session_id, "stop"))
                self.state = SessionState.IDLE
                self._discard_asr_until_speech_final = False
                self._speech_active = False
                self.final_transcript_parts.clear()
        finally:
            if not producer_task.done():
                producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass
            aclose = getattr(llm_stream, "aclose", None)
            if aclose is not None:
                await aclose()
            if self.current_turn_task is asyncio.current_task():
                self.current_turn_task = None
                self.current_cancel_event = None

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
        self._invalidate_capture()
        await self.asr.stop()
        logger.info(f"Session {self.session_id} closed")
