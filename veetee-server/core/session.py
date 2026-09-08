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
from core.conversation import classify_conversation_text
from core.response_audio_cache import ResponseAudioCache
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
        response_audio_cache: Optional[ResponseAudioCache] = None,
        greeting_pool: Optional[list[str]] = None,
    ):
        self.websocket = websocket
        self.config = app_config
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        self.response_audio_cache = response_audio_cache
        
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
        self._wake_generation = 0
        self._pending_wake_task: Optional[asyncio.Task] = None
        self._pending_wake_text: Optional[str] = None
        self._pending_wake_detected_at: Optional[float] = None
        self._fixed_response_kind: Optional[str] = None
        self._greeting_pool: list[str] = [
            str(item).strip()
            for item in (greeting_pool or [])
            if str(item or "").strip()
        ]
        self._greeting_index = 0
        self._greeting_prepare_task: Optional[asyncio.Task] = None
        self._greeting_prewarm_task: Optional[asyncio.Task] = None
        self._closing_reason: Optional[str] = None
        self._conversation_armed = False
        self._activity_revision = 0
        self._last_activity_monotonic = time.monotonic()
        self._idle_not_before = self._last_activity_monotonic
        self._idle_watchdog_task: Optional[asyncio.Task] = None
        self._final_stage_in_progress = False
        self._closed = False
        
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
                pending_wake = self._pending_wake_text is not None
                pending_wake_detected_at = self._pending_wake_detected_at
                self._cancel_pending_wake()
                self._closing_reason = None
                was_speaking = self.state == SessionState.SPEAKING
                requested_mode = str(data.get("mode", "")).strip().lower()
                if requested_mode in {"realtime", "auto", "manual"}:
                    self.listening_mode = requested_mode
                if not pending_wake:
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
                self._mark_conversation_activity("listen_start")
                if pending_wake and self.config.conversation.greeting_enabled:
                    self._start_greeting_response(metric_started_at=pending_wake_detected_at)
            elif state == "detect":
                user_text = data.get("text", "").strip()
                logger.info(f"Listen detect received: '{user_text}'")
                route = classify_conversation_text(
                    user_text,
                    self.config.conversation,
                    source="listen:detect",
                    allow_wake=True,
                    allow_exit=True,
                )
                if route.kind == "wake":
                    await self._handle_wake_detect(user_text)
                elif route.kind == "exit":
                    self._mark_conversation_activity("exit_detect")
                    await self._request_conversation_close("exit_command", user_text=user_text)
                elif route.kind == "chat":
                    self._cancel_pending_wake()
                    self._closing_reason = None
                    self._abort_turn()
                    self._invalidate_capture()
                    self._mark_conversation_activity("detect_chat")
                    await self._trigger_ai_turn(user_text, check_end_intent=True)
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
                route = classify_conversation_text(
                    user_text,
                    self.config.conversation,
                    source=msg_type,
                    allow_wake=False,
                    allow_exit=True,
                )
                self._cancel_pending_wake()
                self._closing_reason = None
                self._mark_conversation_activity(f"{msg_type}_input")
                if route.kind == "exit":
                    await self._request_conversation_close("exit_command", user_text=user_text)
                else:
                    await self._trigger_ai_turn(user_text, check_end_intent=True)

        elif msg_type == MessageType.ABORT:
            reason = data.get("reason", "")
            logger.info(f"Client requested abort (reason: {reason})")
            self._cancel_pending_wake()
            self._closing_reason = None
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
            self._mark_conversation_activity("abort")

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
        current_task = asyncio.current_task()
        if (
            self.current_turn_task
            and not self.current_turn_task.done()
            and self.current_turn_task is not current_task
        ):
            self.current_turn_task.cancel()
            self.current_turn_task = None
        self._fixed_response_kind = None

    def _invalidate_capture(self):
        self._capture_generation += 1
        invalidate = getattr(self.asr, "invalidate_capture", None)
        if invalidate is not None:
            invalidate(self._capture_generation)

    def _owns_turn(self, turn_generation: int) -> bool:
        return self.is_active and self._turn_generation == turn_generation

    def _cancel_pending_wake(self):
        self._wake_generation += 1
        task = self._pending_wake_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._pending_wake_task = None
        self._pending_wake_text = None
        self._pending_wake_detected_at = None

    def _mark_conversation_activity(self, reason: str):
        if not self.config.conversation.enabled or not self.is_active:
            return
        self._conversation_armed = True
        self._activity_revision += 1
        self._last_activity_monotonic = time.monotonic()
        logger.debug(
            "conversation_activity session=%s revision=%d reason=%s",
            self.session_id,
            self._activity_revision,
            reason,
        )
        if (
            self.config.conversation.idle_timeout_seconds > 0
            and (self._idle_watchdog_task is None or self._idle_watchdog_task.done())
        ):
            self._idle_watchdog_task = asyncio.create_task(self._idle_watchdog())

    def _mark_response_complete(self, playback_guard_until: float):
        if not self.config.conversation.enabled:
            return
        self._activity_revision += 1
        self._last_activity_monotonic = time.monotonic()
        self._idle_not_before = max(self._last_activity_monotonic, playback_guard_until)

    def _idle_busy(self) -> bool:
        return (
            self._pending_wake_text is not None
            or self._fixed_response_kind is not None
            or self._closing_reason is not None
            or self._speech_active
            or self._final_stage_in_progress
            or self.state in (SessionState.THINKING, SessionState.SPEAKING)
        )

    async def _idle_watchdog(self):
        timeout = float(self.config.conversation.idle_timeout_seconds)
        if timeout <= 0:
            return
        try:
            while self.is_active and self._conversation_armed:
                revision = self._activity_revision
                deadline = max(self._last_activity_monotonic, self._idle_not_before) + timeout
                delay = max(0.0, deadline - time.monotonic())
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self.is_active or not self._conversation_armed:
                    return
                if revision != self._activity_revision:
                    continue
                if self._idle_busy():
                    await asyncio.sleep(min(0.25, max(0.05, timeout / 4.0)))
                    continue
                if time.monotonic() < max(self._last_activity_monotonic, self._idle_not_before) + timeout:
                    continue
                logger.info(
                    "conversation_idle_timeout session=%s revision=%d timeout=%.0fs",
                    self.session_id,
                    revision,
                    timeout,
                )
                await self._request_conversation_close("idle_timeout")
                return
        except asyncio.CancelledError:
            pass

    async def _handle_wake_detect(self, user_text: str):
        if self._pending_wake_text is not None or self._fixed_response_kind == "greeting":
            logger.info("Deduplicating wake detect while greeting cycle is active")
            return

        self._closing_reason = None
        was_speaking = self.state == SessionState.SPEAKING
        self._abort_turn()
        self._invalidate_capture()
        self._playback_guard_until = 0.0
        if was_speaking:
            await self.send_text(make_tts_message(self.session_id, "stop"))
        self.state = SessionState.LISTENING
        self.final_transcript_parts.clear()
        self._speech_active = False
        self._discard_asr_until_speech_final = False
        self.processed_transcript = ""
        self._mark_conversation_activity("wake_detect")

        if not self.config.conversation.greeting_enabled:
            return

        self._wake_generation += 1
        wake_generation = self._wake_generation
        self._pending_wake_text = user_text
        self._pending_wake_detected_at = time.perf_counter()
        self._pending_wake_task = asyncio.create_task(
            self._wake_after_start_window(wake_generation)
        )

    async def _wake_after_start_window(self, wake_generation: int):
        try:
            await asyncio.sleep(self.config.conversation.wake_start_wait_ms / 1000.0)
            if (
                not self.is_active
                or wake_generation != self._wake_generation
                or self._pending_wake_text is None
            ):
                return
            detected_at = self._pending_wake_detected_at
            self._pending_wake_task = None
            self._pending_wake_text = None
            self._pending_wake_detected_at = None
            self._start_greeting_response(metric_started_at=detected_at)
        except asyncio.CancelledError:
            pass

    def refresh_ai_greetings(self):
        if not self.config.conversation.enabled or not self.config.conversation.greeting_enabled:
            return
        task = self._greeting_prepare_task
        if task and not task.done():
            task.cancel()
        prewarm_task = self._greeting_prewarm_task
        if prewarm_task and not prewarm_task.done():
            prewarm_task.cancel()
        self._greeting_prewarm_task = None
        self._greeting_pool = []
        self._greeting_index = 0
        if self.config.conversation.greeting_ai_enabled:
            self._greeting_prepare_task = asyncio.create_task(self._prepare_ai_greetings())

    def _ensure_greeting_prepare_task(self) -> Optional[asyncio.Task]:
        if not self.config.conversation.greeting_ai_enabled:
            return None
        task = self._greeting_prepare_task
        if task is None or (task.done() and not self._greeting_pool):
            task = asyncio.create_task(self._prepare_ai_greetings())
            self._greeting_prepare_task = task
        return task

    async def _prepare_ai_greetings(self):
        generator = getattr(self.llm_engine, "generate_greetings", None)
        if generator is None:
            return
        timeout = max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0)
        try:
            generated = await asyncio.wait_for(
                generator(self.config.conversation.greeting_pool_size),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("AI greeting generation failed: %s", exc)
            return

        greetings = []
        for item in generated or []:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in greetings:
                greetings.append(cleaned)
        if not greetings:
            logger.warning("AI greeting generation returned no usable phrases")
            return

        self._greeting_pool = greetings[: self.config.conversation.greeting_pool_size]
        self._greeting_index = 0
        logger.info("AI greeting pool ready session=%s count=%d", self.session_id, len(self._greeting_pool))
        if self.config.conversation.audio_cache_enabled and self.response_audio_cache is not None:
            self._greeting_prewarm_task = asyncio.create_task(
                self.response_audio_cache.prewarm(
                    self._greeting_pool,
                    self.config.conversation.fixed_response_timeout_seconds,
                )
            )

    def _next_greeting_text(self) -> str:
        if self._greeting_pool:
            text = self._greeting_pool[self._greeting_index % len(self._greeting_pool)]
            self._greeting_index += 1
            return text
        return self.config.conversation.greeting_text.strip()

    def _start_greeting_response(self, *, metric_started_at: Optional[float] = None):
        self._abort_turn()
        cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = cancel_event
        self._fixed_response_kind = "greeting"
        self.current_turn_task = asyncio.create_task(
            self._process_ai_greeting_response(
                cancel_event,
                turn_generation,
                metric_started_at=metric_started_at,
            )
        )

    async def _process_ai_greeting_response(
        self,
        cancel_event: asyncio.Event,
        turn_generation: int,
        *,
        metric_started_at: Optional[float],
    ):
        try:
            if not self._greeting_pool and self.config.conversation.greeting_ai_enabled:
                prepare_task = self._ensure_greeting_prepare_task()
                if prepare_task is not None:
                    timeout = max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0)
                    try:
                        await asyncio.wait_for(asyncio.shield(prepare_task), timeout=timeout)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "AI greeting generation timed out after %.0f ms",
                            timeout * 1000,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("AI greeting preparation failed: %s", exc)

            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                return
            text = self._next_greeting_text()
            if not text:
                logger.info("Skipping greeting because AI generation failed and no fallback is configured")
                self.state = (
                    SessionState.IDLE
                    if self.listening_mode == "manual"
                    else SessionState.LISTENING
                )
                return
            await self._process_fixed_response(
                text,
                cancel_event,
                turn_generation,
                kind="greeting",
                close_after=False,
                closing_reason=None,
                metric_started_at=metric_started_at,
            )
        finally:
            if self.current_turn_task is asyncio.current_task():
                self.current_turn_task = None
                self.current_cancel_event = None
                self._fixed_response_kind = None

    async def _has_ai_end_intent(self, user_text: str) -> bool:
        if not self.config.conversation.end_intent_ai_enabled:
            return False
        classifier = getattr(self.llm_engine, "classify_end_intent", None)
        if classifier is None:
            return False
        timeout = max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0)
        try:
            result = await asyncio.wait_for(
                classifier(user_text, self.dialogue.get_messages_for_llm()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("AI end-intent classification timed out after %.0f ms", timeout * 1000)
            return False
        except Exception as exc:
            logger.warning("AI end-intent classification failed: %s", exc)
            return False
        if result:
            logger.info("AI end-intent detected session=%s text=%r", self.session_id, user_text)
        return bool(result)

    def _start_ai_goodbye_response(self, reason: str, user_text: str = ""):
        self._abort_turn()
        cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = cancel_event
        self._fixed_response_kind = "goodbye"
        self.current_turn_task = asyncio.create_task(
            self._process_ai_goodbye_response(
                reason,
                user_text,
                cancel_event,
                turn_generation,
            )
        )

    async def _process_ai_goodbye_response(
        self,
        reason: str,
        user_text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
    ):
        text = ""
        if self.config.conversation.goodbye_ai_enabled:
            generator = getattr(self.llm_engine, "generate_goodbye", None)
            if generator is not None:
                timeout = max(0.1, self.config.conversation.ai_control_timeout_ms / 1000.0)
                try:
                    text = await asyncio.wait_for(
                        generator(
                            self.dialogue.get_messages_for_llm(),
                            reason=reason,
                            user_text=user_text,
                        ),
                        timeout=timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("AI goodbye generation failed: %s", exc)
        text = str(text or "").strip() or self.config.conversation.goodbye_text.strip()
        if cancel_event.is_set() or not self._owns_turn(turn_generation):
            return
        if not text:
            await self._close_transport_and_session(reason)
            return
        await self._process_live_fixed_response(
            text,
            cancel_event,
            turn_generation,
            kind="goodbye",
            close_after=True,
            closing_reason=reason,
            metric_started_at=None,
        )

    def _start_fixed_response(
        self,
        text: str,
        *,
        kind: str,
        close_after: bool = False,
        closing_reason: Optional[str] = None,
        metric_started_at: Optional[float] = None,
    ):
        self._abort_turn()
        cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = cancel_event
        self._fixed_response_kind = kind
        self.current_turn_task = asyncio.create_task(
            self._process_fixed_response(
                text,
                cancel_event,
                turn_generation,
                kind=kind,
                close_after=close_after,
                closing_reason=closing_reason,
                metric_started_at=metric_started_at,
            )
        )

    async def _collect_fixed_audio(self, text: str, cancel_event: asyncio.Event) -> tuple[bytes, ...]:
        frames = []
        async for frame in self.tts_engine.stream_sentence_to_opus(text, cancel_event):
            if cancel_event.is_set():
                break
            if frame:
                frames.append(bytes(frame))
        if not frames and not cancel_event.is_set():
            raise RuntimeError("TTS produced no audio for fixed response")
        return tuple(frames)

    async def _process_live_fixed_response(
        self,
        text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
        *,
        kind: str,
        close_after: bool,
        closing_reason: Optional[str],
        metric_started_at: Optional[float],
    ):
        """Stream dynamic fixed text immediately instead of buffering the whole clip."""
        started = time.perf_counter()
        sent_start = False
        first_binary_sent = False
        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )
        try:
            if not self._owns_turn(turn_generation):
                return
            self.state = SessionState.THINKING
            if not await self.send_text(make_tts_message(self.session_id, "start")):
                raise ConnectionError("failed to send live fixed-response tts:start")
            sent_start = True
            if not await self.send_text(make_tts_message(self.session_id, "sentence_start", text)):
                raise ConnectionError("failed to send live fixed-response sentence_start")
            self.state = SessionState.SPEAKING

            async for opus_frame in self.tts_engine.stream_sentence_to_opus(text, cancel_event):
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return
                if not await pacer.wait_for_send(cancel_event):
                    return
                if not await self.send_binary(pack_audio_payload(opus_frame, self.version)):
                    raise ConnectionError("failed to send live fixed-response audio")
                pacer.record_frame_sent()
                if pacer.playback_end is not None:
                    self._playback_guard_until = max(self._playback_guard_until, pacer.playback_end)
                if not first_binary_sent:
                    first_binary_sent = True
                    metric_origin = metric_started_at if metric_started_at is not None else started
                    logger.info(
                        "fixed_response_first_binary session=%s kind=%s elapsed_ms=%.0f live=true",
                        self.session_id,
                        kind,
                        (time.perf_counter() - metric_origin) * 1000.0,
                    )

            if not first_binary_sent and not cancel_event.is_set():
                raise RuntimeError("TTS produced no audio for live fixed response")

            if close_after:
                drain_seconds = pacer.estimated_lead_ms() / 1000.0
                drain_seconds += self.config.conversation.close_grace_ms / 1000.0
                logger.info(
                    "conversation_close_drain session=%s reason=%s estimated_ms=%.0f",
                    self.session_id,
                    closing_reason,
                    drain_seconds * 1000.0,
                )
                if not await self._wait_cancelable(drain_seconds, cancel_event):
                    return

            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                return
            if not await self.send_text(make_tts_message(self.session_id, "stop")):
                raise ConnectionError("failed to send live fixed-response tts:stop")

            if close_after:
                await self._close_transport_and_session(closing_reason or kind)
                return

            self.state = SessionState.IDLE if self.listening_mode == "manual" else SessionState.LISTENING
            self.dialogue.add_assistant_message(text)
            self._mark_response_complete(self._playback_guard_until)
        except asyncio.CancelledError:
            logger.info("fixed_response_cancelled session=%s kind=%s", self.session_id, kind)
        except Exception as exc:
            logger.warning(
                "fixed_response_failed session=%s kind=%s error=%s",
                self.session_id,
                kind,
                exc,
            )
            if self._owns_turn(turn_generation) and sent_start:
                await self.send_text(make_tts_message(self.session_id, "stop"))
            if close_after and self.is_active:
                await self._close_transport_and_session(closing_reason or kind)
            elif self._owns_turn(turn_generation):
                self.state = SessionState.IDLE if self.listening_mode == "manual" else SessionState.LISTENING
        finally:
            if self.current_turn_task is asyncio.current_task():
                self.current_turn_task = None
                self.current_cancel_event = None
                self._fixed_response_kind = None

    async def _wait_cancelable(self, seconds: float, cancel_event: asyncio.Event) -> bool:
        if seconds <= 0:
            return not cancel_event.is_set()
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=seconds)
            return False
        except asyncio.TimeoutError:
            return not cancel_event.is_set()

    async def _process_fixed_response(
        self,
        text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
        *,
        kind: str,
        close_after: bool,
        closing_reason: Optional[str],
        metric_started_at: Optional[float],
    ):
        started = time.perf_counter()
        sent_start = False
        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )
        try:
            if not self._owns_turn(turn_generation):
                return
            self.state = SessionState.THINKING
            timeout = self.config.conversation.fixed_response_timeout_seconds
            if self.config.conversation.audio_cache_enabled and self.response_audio_cache is not None:
                cache_result = await self.response_audio_cache.get_or_fill(text, timeout)
                frames = cache_result.frames
                logger.info(
                    "fixed_response_audio_ready session=%s kind=%s cache_hit=%s cold_ms=%.0f",
                    self.session_id,
                    kind,
                    cache_result.hit,
                    cache_result.synthesis_ms,
                )
            else:
                frames = await asyncio.wait_for(
                    self._collect_fixed_audio(text, cancel_event),
                    timeout=timeout,
                )
                logger.info(
                    "fixed_response_audio_ready session=%s kind=%s cache_hit=false cold_ms=%.0f",
                    self.session_id,
                    kind,
                    (time.perf_counter() - started) * 1000.0,
                )

            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                return
            if not await self.send_text(make_tts_message(self.session_id, "start")):
                raise ConnectionError("failed to send fixed-response tts:start")
            sent_start = True
            if not await self.send_text(make_tts_message(self.session_id, "sentence_start", text)):
                raise ConnectionError("failed to send fixed-response sentence_start")
            self.state = SessionState.SPEAKING

            first_binary_sent = False
            for opus_frame in frames:
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return
                if not await pacer.wait_for_send(cancel_event):
                    return
                packet = pack_audio_payload(opus_frame, self.version)
                if not await self.send_binary(packet):
                    raise ConnectionError("failed to send fixed-response audio")
                pacer.record_frame_sent()
                if pacer.playback_end is not None:
                    self._playback_guard_until = max(self._playback_guard_until, pacer.playback_end)
                if not first_binary_sent:
                    first_binary_sent = True
                    metric_origin = metric_started_at if metric_started_at is not None else started
                    logger.info(
                        "fixed_response_first_binary session=%s kind=%s elapsed_ms=%.0f",
                        self.session_id,
                        kind,
                        (time.perf_counter() - metric_origin) * 1000.0,
                    )

            if close_after:
                drain_seconds = pacer.estimated_lead_ms() / 1000.0
                drain_seconds += self.config.conversation.close_grace_ms / 1000.0
                logger.info(
                    "conversation_close_drain session=%s reason=%s estimated_ms=%.0f",
                    self.session_id,
                    closing_reason,
                    drain_seconds * 1000.0,
                )
                if not await self._wait_cancelable(drain_seconds, cancel_event):
                    return

            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                return
            if not await self.send_text(make_tts_message(self.session_id, "stop")):
                raise ConnectionError("failed to send fixed-response tts:stop")

            if close_after:
                await self._close_transport_and_session(closing_reason or kind)
                return

            self.state = (
                SessionState.IDLE
                if self.listening_mode == "manual"
                else SessionState.LISTENING
            )
            self.dialogue.add_assistant_message(text)
            self._mark_response_complete(self._playback_guard_until)
            logger.info(
                "fixed_response_complete session=%s kind=%s total_ms=%.0f audio_ms=%.0f tail_ms=%.0f",
                self.session_id,
                kind,
                (time.perf_counter() - started) * 1000.0,
                pacer.total_audio_ms,
                pacer.estimated_lead_ms(),
            )
        except asyncio.CancelledError:
            logger.info("fixed_response_cancelled session=%s kind=%s", self.session_id, kind)
        except Exception as exc:
            logger.warning(
                "fixed_response_failed session=%s kind=%s error=%s",
                self.session_id,
                kind,
                exc,
            )
            if self._owns_turn(turn_generation) and sent_start:
                await self.send_text(make_tts_message(self.session_id, "stop"))
            if close_after and self.is_active:
                await self._close_transport_and_session(closing_reason or kind)
            elif self._owns_turn(turn_generation):
                self.state = (
                    SessionState.IDLE
                    if self.listening_mode == "manual"
                    else SessionState.LISTENING
                )
        finally:
            if self.current_turn_task is asyncio.current_task():
                self.current_turn_task = None
                self.current_cancel_event = None
                self._fixed_response_kind = None

    async def _request_conversation_close(self, reason: str, *, user_text: str = ""):
        if not self.config.conversation.enabled or not self.is_active:
            return
        if self._closing_reason is not None:
            return
        self._cancel_pending_wake()
        was_speaking = self.state == SessionState.SPEAKING
        self._abort_turn()
        self._invalidate_capture()
        self.final_transcript_parts.clear()
        self._speech_active = False
        self._discard_asr_until_speech_final = False
        self.processed_transcript = ""
        self._closing_reason = reason
        logger.info("conversation_close_requested session=%s reason=%s", self.session_id, reason)
        if was_speaking:
            await self.send_text(make_tts_message(self.session_id, "stop"))

        if self.config.conversation.goodbye_enabled:
            self._start_ai_goodbye_response(reason, user_text)
        else:
            self.current_turn_task = asyncio.create_task(self._close_transport_and_session(reason))

    async def _close_transport_and_session(self, reason: str):
        logger.info("conversation_close_commit session=%s reason=%s", self.session_id, reason)
        await self._close_websocket(code=1000, reason=reason)
        await self.close(close_transport=False)

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

        if self._pending_wake_text is not None:
            self._cancel_pending_wake()

        if self._closing_reason is not None:
            logger.info(
                "conversation_close_cancelled_by_user_speech session=%s reason=%s",
                self.session_id,
                self._closing_reason,
            )
            self._closing_reason = None
            self._abort_turn()
            self.state = SessionState.LISTENING

        self._speech_active = True
        self._mark_conversation_activity("speech_start")
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
            self._final_stage_in_progress = True
            try:
                final_stage_started = time.perf_counter()
                raw_final_text = " ".join(self.final_transcript_parts).strip() or transcript.strip()
                self.final_transcript_parts.clear()
                raw_route = classify_conversation_text(
                    raw_final_text,
                    self.config.conversation,
                    source="asr_final",
                    allow_wake=False,
                    allow_exit=True,
                )
                if raw_route.kind == "exit":
                    final_text = raw_final_text
                else:
                    final_text = await self._maybe_correct_asr_transcript(raw_final_text)
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
                if raw_route.kind == "exit":
                    self._mark_conversation_activity("asr_exit")
                    await self._request_conversation_close("exit_command", user_text=raw_final_text)
                elif final_text:
                    self._mark_conversation_activity("asr_final")
                    await self._trigger_ai_turn(final_text, check_end_intent=True)
            finally:
                self._final_stage_in_progress = False

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

    async def _trigger_ai_turn(self, transcript: str, *, check_end_intent: bool = False):
        if not transcript.strip() or transcript == self.processed_transcript:
            return

        self._cancel_pending_wake()
        self._closing_reason = None
        self._mark_conversation_activity("ai_turn")
        
        self.processed_transcript = transcript
        logger.info(f"Triggering AI Turn for prompt: '{transcript}'")
        
        # Abort any prior playing turn
        self._abort_turn()
        
        # Launch AI streaming response pipeline with dedicated cancel event
        turn_cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = turn_cancel_event
        self.current_turn_task = asyncio.create_task(
            self._process_ai_response(
                transcript,
                turn_cancel_event,
                turn_generation,
                check_end_intent=check_end_intent,
            )
        )

    async def _process_ai_response(
        self,
        user_text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
        *,
        check_end_intent: bool = False,
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
        inline_control_streamer = (
            getattr(self.llm_engine, "stream_chat_with_control", None)
            if check_end_intent and self.config.conversation.end_intent_ai_enabled
            else None
        )
        use_inline_control = callable(inline_control_streamer)
        end_intent_task = None
        if (
            check_end_intent
            and self.config.conversation.end_intent_ai_enabled
            and not use_inline_control
        ):
            # Compatibility fallback for LLM providers that do not yet expose
            # inline conversation control. The active OmniRoute provider uses
            # one call for response + end-intent.
            end_intent_task = asyncio.create_task(self._has_ai_end_intent(user_text))
        
        full_reply_clauses = []
        is_first_clause = True
        first_binary_sent = False
        inline_close_reason: Optional[str] = None
        clause_queue: asyncio.Queue = asyncio.Queue(maxsize=3)
        queue_done = object()
        producer_errors = []
        llm_stream = (
            inline_control_streamer(messages)
            if use_inline_control
            else self.llm_engine.stream_chat(messages)
        )

        async def produce_clauses():
            try:
                async for item in llm_stream:
                    if cancel_event.is_set():
                        return
                    if isinstance(item, tuple) and len(item) == 3:
                        clause, emotion, inline_end_intent = item
                    else:
                        clause, emotion = item
                        inline_end_intent = None
                    await clause_queue.put((clause, emotion, inline_end_intent))
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

                clause, emotion, inline_end_intent = queued_item
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    logger.info("Turn cancelled.")
                    return

                if is_first_clause:
                    if inline_end_intent is True:
                        inline_close_reason = "ai_end_intent"
                        self._cancel_pending_wake()
                        self._invalidate_capture()
                        self.final_transcript_parts.clear()
                        self._speech_active = False
                        self._discard_asr_until_speech_final = False
                        self.processed_transcript = ""
                        self._closing_reason = inline_close_reason
                        logger.info(
                            "AI end-intent detected inline session=%s text=%r",
                            self.session_id,
                            user_text,
                        )
                        logger.info(
                            "conversation_close_requested session=%s reason=%s",
                            self.session_id,
                            inline_close_reason,
                        )
                    elif end_intent_task is not None:
                        if await end_intent_task:
                            logger.info(
                                "Discarding speculative chat response because AI end-intent won session=%s",
                                self.session_id,
                            )
                            await self._request_conversation_close(
                                "ai_end_intent",
                                user_text=user_text,
                            )
                            return
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
                if inline_close_reason:
                    drain_seconds = pacer.estimated_lead_ms() / 1000.0
                    drain_seconds += self.config.conversation.close_grace_ms / 1000.0
                    logger.info(
                        "conversation_close_drain session=%s reason=%s estimated_ms=%.0f",
                        self.session_id,
                        inline_close_reason,
                        drain_seconds * 1000.0,
                    )
                    if not await self._wait_cancelable(drain_seconds, cancel_event):
                        return

                await self.send_text(make_tts_message(self.session_id, "stop"))
                logger.info(
                    "Post-ASR tts:stop sent in %.3fs",
                    time.perf_counter() - t_start,
                )
                
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
                if inline_close_reason:
                    await self._close_transport_and_session(inline_close_reason)
                    return

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
                self._mark_response_complete(self._playback_guard_until)
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
            if end_intent_task is not None and not end_intent_task.done():
                end_intent_task.cancel()
                try:
                    await end_intent_task
                except asyncio.CancelledError:
                    pass
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

    async def send_text(self, text: str) -> bool:
        if not self.is_active:
            return False
        try:
            result = await self.websocket.send(text)
            return result is not False
        except Exception as e:
            logger.debug(f"Error sending text: {e}")
            return False

    async def send_binary(self, data: bytes) -> bool:
        if not self.is_active:
            return False
        try:
            result = await self.websocket.send(data)
            return result is not False
        except Exception as e:
            logger.debug(f"Error sending binary: {e}")
            return False

    async def _close_websocket(self, code: int = 1000, reason: str = ""):
        close = getattr(self.websocket, "close", None)
        if close is None:
            return
        try:
            result = close(code=code, reason=reason)
            if asyncio.iscoroutine(result):
                await result
        except TypeError:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug("Error closing websocket: %s", exc)

    async def close(self, close_transport: bool = True):
        if self._closed:
            return
        self._closed = True
        self.is_active = False
        self._conversation_armed = False
        self._closing_reason = None

        greeting_task = self._greeting_prepare_task
        if greeting_task and not greeting_task.done():
            greeting_task.cancel()
        self._greeting_prepare_task = None
        greeting_prewarm_task = self._greeting_prewarm_task
        if greeting_prewarm_task and not greeting_prewarm_task.done():
            greeting_prewarm_task.cancel()
        self._greeting_prewarm_task = None

        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        self._turn_generation += 1
        current = asyncio.current_task()
        tasks = []
        for task in (
            self.current_turn_task,
            self._pending_wake_task,
            self._idle_watchdog_task,
        ):
            if task is not None and not task.done() and task is not current:
                task.cancel()
                tasks.append(task)
        self.current_turn_task = None
        self.current_cancel_event = None
        self._pending_wake_task = None
        self._idle_watchdog_task = None
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._invalidate_capture()
        try:
            await self.asr.stop()
        finally:
            if close_transport:
                await self._close_websocket(code=1000, reason="session_closed")
        logger.info(f"Session {self.session_id} closed")
