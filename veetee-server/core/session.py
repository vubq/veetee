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
from core.providers.tts.base import open_tts_stream
from core.response_audio_cache import ResponseAudioCache
from core.context_builder import ContextBuilder
from core.clock_context import clock_context
from core.intent import PendingActionStore
from core.ai_contract import (
    CONFIRMATION_TOOL_NAME,
    MEMORY_TOOL_NAME,
    SEMANTIC_SYSTEM_PROMPT,
    semantic_tools,
)
from core.receipts import make_receipt
from core.memory.models import MemoryApplyResult, MemoryProposal, SessionMemoryFact
from core.memory.retrieval import MemoryRetriever
from core.memory.store import MemoryStore
from core.tools.builtin.calculator import calculator_descriptor
from core.tools.builtin.time_tool import time_descriptor
from core.tools.executor import ToolExecutor
from core.tools.mcp_device import MCPDeviceClient
from core.tools.registry import ToolRegistry, ToolValidationError, validate_arguments
from core.tools.results import ToolResult, ToolStatus
from core.turn_events import (
    CompletedEvent,
    ConfirmationDecisionEvent,
    ControlEvent,
    FailedEvent,
    MemoryProposalEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)
from core.turn_metrics import TurnMetricsRecorder, TurnTraceStore, activate_trace, mark_current, reset_trace
from core.turn_runner import TurnRunner
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
        turn_trace_store: Optional[TurnTraceStore] = None,
    ):
        self.websocket = websocket
        self.config = app_config
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        self.response_audio_cache = response_audio_cache
        
        self.session_id = str(uuid.uuid4()).replace("-", "")
        self.turn_metrics = TurnMetricsRecorder(
            self.session_id,
            app_config,
            shared_store=turn_trace_store,
        )
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
        self.turn_runner = TurnRunner(self.llm_engine)
        self._session_memory: list[SessionMemoryFact] = []
        self._memory_store: Optional[MemoryStore] = None
        self._memory_owner_id: Optional[str] = None
        retriever = None
        if self.config.memory.enabled and self.config.memory.durable_enabled:
            owner_id = self.config.memory.trusted_owner_id.strip()
            if owner_id:
                self._memory_owner_id = owner_id
                self._memory_store = MemoryStore(self.config.memory.database_path)
                retriever = MemoryRetriever(self._memory_store, top_k=self.config.memory.top_k)
        self.context_builder = ContextBuilder(
            retriever,
            lookup_timeout_ms=self.config.memory.lookup_timeout_ms,
            top_k=self.config.memory.top_k,
            max_memory_chars=self.config.memory.max_memory_chars,
        )
        descriptors = []
        if self.config.tools.enabled:
            descriptors = [time_descriptor(self.config.server.timezone), calculator_descriptor()]
        self.tool_registry = ToolRegistry(descriptors)
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            max_calls_per_turn=self.config.tools.max_calls_per_turn,
        )
        self.pending_actions = PendingActionStore()
        self.mcp_device: Optional[MCPDeviceClient] = None
        if self.config.tools.enabled and self.config.tools.mcp_device_enabled:
            self.mcp_device = MCPDeviceClient(
                send_payload=self._send_mcp_payload,
                registry=self.tool_registry,
            )
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
                utterance_queue_max=self.config.asr.utterance_queue_max,
                max_utterance_ms=self.config.asr.max_utterance_ms,
                diagnostic_capture_enabled=self.config.asr.diagnostic_capture_enabled,
                diagnostic_capture_dir=self.config.asr.diagnostic_capture_dir,
                diagnostic_capture_max_files=self.config.asr.diagnostic_capture_max_files,
                metrics_recorder=self.turn_metrics,
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

    def _fit_llm_context(
        self,
        messages: list[Dict[str, Any]],
        *,
        tools: list[Dict[str, Any]],
        detect_end_intent: bool,
    ) -> list[Dict[str, Any]]:
        # One shared request assembly for budgeting and provider dispatch.
        # Persona, semantic/control prompts, history, memory/RAG, pending
        # actions, schemas, receipts and output reserve are all counted.
        # Nothing is appended after fit without recounting.
        fixed_system_messages = []
        base_system_prompt = str(getattr(self.llm_engine, "system_prompt", "") or "").strip()
        if base_system_prompt:
            fixed_system_messages.append(base_system_prompt)
        fixed_system_messages.append(SEMANTIC_SYSTEM_PROMPT)
        if detect_end_intent:
            control_prompt = str(
                getattr(self.llm_engine, "INLINE_CONVERSATION_CONTROL_PROMPT", "") or ""
            ).strip()
            if control_prompt:
                fixed_system_messages.append(control_prompt)

        fitted = self.context_builder.fit_to_budget(
            messages,
            system_messages=fixed_system_messages,
            tools=tools,
            max_context_tokens=self.config.latency.context_max_tokens,
            reserve_output_tokens=self.config.llm.max_tokens,
            chars_per_token=self.config.latency.context_chars_per_token,
        )
        persona_version = int(getattr(self.llm_engine, "persona_version", 0) or 0)
        self.context_builder.last_budget["persona_version"] = persona_version
        self.context_builder.last_budget["catalog_hash"] = self.tool_registry.catalog_fingerprint()
        mark_current("context_budget", **self.context_builder.last_budget)
        return fitted

    def _persona_snapshot(self) -> int:
        return int(getattr(self.llm_engine, "persona_version", 0) or 0)

    def _semantic_memory_schema(self) -> Dict[str, Any]:
        for tool in semantic_tools(memory_enabled=True, pending_action=False):
            if tool.get("function", {}).get("name") == MEMORY_TOOL_NAME:
                return dict(tool["function"].get("parameters") or {})
        return {"type": "object"}

    def _semantic_confirmation_schema(self) -> Dict[str, Any]:
        for tool in semantic_tools(memory_enabled=False, pending_action=True):
            if tool.get("function", {}).get("name") == CONFIRMATION_TOOL_NAME:
                return dict(tool["function"].get("parameters") or {})
        return {"type": "object"}

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
            if self.mcp_device is not None:
                await self.mcp_device.on_hello(features.get("mcp") is True)
            logger.info(
                "Handshake acknowledged for session %s (Ver=%s, mode=%s, reported_server_aec=%s, barge_in_policy=%s, input_frame=%sms)",
                self.session_id,
                self.version,
                self.listening_mode,
                self.server_side_aec_requested,
                self.barge_in_policy,
                self.codec.in_frame_duration_ms,
            )

        elif msg_type == MessageType.MCP:
            if self.mcp_device is not None:
                await self.mcp_device.handle_message(data)

        elif msg_type == MessageType.LISTEN:
            state = data.get("state")
            if state == "start":
                pending_wake = self._pending_wake_text is not None
                pending_wake_text = self._pending_wake_text or ""
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
                if pending_wake and pending_wake_text.strip():
                    await self._trigger_ai_turn(
                        pending_wake_text,
                        check_end_intent=True,
                        source="wake_detect",
                    )
            elif state == "detect":
                user_text = data.get("text", "").strip()
                logger.info(f"Listen detect received: '{user_text}'")
                if user_text:
                    await self._handle_wake_detect(user_text)
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
                self._cancel_pending_wake()
                self._closing_reason = None
                self._mark_conversation_activity(f"{msg_type}_input")
                await self._trigger_ai_turn(
                    user_text,
                    check_end_intent=True,
                    source=msg_type,
                )

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
        # Transcript de-duplication is scoped to one capture. The same phrase
        # spoken again after a new capture is a new user turn.
        self.processed_transcript = ""
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
                ended = await self._evaluate_idle_semantics(revision, timeout)
                if ended:
                    return
        except asyncio.CancelledError:
            pass

    async def _evaluate_idle_semantics(self, revision: int, timeout: float) -> bool:
        """Deterministic conversational idle deadline.

        After `idle_timeout_seconds` with no conversational interaction
        (no user question and no AI answer), the session always ends: one
        bounded LLM inference generates a short goodbye, it is played, then
        the transport/session is closed. There is no AI-decided [continue]
        loop — a quiet session must not linger forever. A new user turn that
        arrives mid-flow bumps the activity revision and cancels this path.
        """
        messages = list(self.dialogue.get_messages_for_llm())
        messages.append({
            "role": "system",
            "content": (
                "Sự kiện hệ thống: hội thoại đã không có tương tác "
                f"{timeout:.0f} giây (không có câu hỏi của người dùng và không có câu trả lời nào). "
                "Phiên sắp kết thúc và sẽ ngắt ngay sau câu này. "
                "Hãy tạo một câu chào tạm biệt ngắn, tự nhiên, đúng tính cách trong prompt hệ thống và "
                "phù hợp ngữ cảnh hội thoại — đại ý nếu không cần gì nữa thì xin phép đi trước, "
                "có gì cứ gọi lại sau. Đây là câu chào kết thúc, không phải câu hỏi: "
                "không hỏi người dùng có cần giúp gì không, không hỏi sao im lặng. "
                "Chỉ trả về đúng một câu chào, không thêm gì khác."
            ),
        })
        speech: list[str] = []
        try:
            async for event in self.turn_runner.stream(
                messages,
                tools=[],
                detect_end_intent=True,
                tool_choice="none",
                first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                total_timeout_ms=min(
                    self.config.latency.total_turn_timeout_ms,
                    max(100, self.config.conversation.ai_control_timeout_ms),
                ),
            ):
                if revision != self._activity_revision or not self.is_active:
                    return False
                if isinstance(event, SpeechSegmentEvent):
                    speech.append(event.text)
                elif isinstance(event, FailedEvent):
                    raise RuntimeError(event.error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("idle goodbye generation failed session=%s error=%s", self.session_id, exc)

        if revision != self._activity_revision or not self.is_active:
            return False
        self._closing_reason = "idle_timeout"
        text = " ".join(part.strip() for part in speech if part.strip()).strip()
        if text and self.config.conversation.goodbye_enabled:
            self._start_fixed_response(
                text,
                kind="idle_goodbye",
                close_after=True,
                closing_reason="idle_timeout",
            )
        else:
            await self._close_transport_and_session("idle_timeout")
        return True

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
            user_text = self._pending_wake_text
            self._pending_wake_task = None
            self._pending_wake_text = None
            self._pending_wake_detected_at = None
            if user_text and user_text.strip():
                await self._trigger_ai_turn(
                    user_text,
                    check_end_intent=True,
                    source="listen_detect",
                )
        except asyncio.CancelledError:
            pass

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
        async for frame in open_tts_stream(
            self.tts_engine,
            text,
            cancel_event,
            priority="live",
            queue_deadline_seconds=self.config.latency.total_turn_timeout_ms / 1000.0,
        ):
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

            async for opus_frame in open_tts_stream(
                self.tts_engine,
                text,
                cancel_event,
                priority="live",
                queue_deadline_seconds=self.config.latency.total_turn_timeout_ms / 1000.0,
            ):
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

    async def _send_cached_error_fallback(
        self,
        cancel_event: asyncio.Event,
        turn_generation: int,
    ) -> bool:
        """Send the startup AI-generated recovery clip without invoking LLM/TTS."""
        if self.response_audio_cache is None:
            return False
        try:
            recovery = await self.response_audio_cache.get_recovery()
        except (KeyError, RuntimeError) as exc:
            logger.info("cached_error_fallback_unavailable session=%s reason=%s", self.session_id, exc)
            return False
        except Exception as exc:
            logger.warning("cached_error_fallback_lookup_failed session=%s error=%s", self.session_id, exc)
            return False

        frames = tuple(recovery.frames or ())
        if not frames or cancel_event.is_set() or not self._owns_turn(turn_generation):
            return False

        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )
        sent_start = False
        sent_audio = False
        try:
            if not await self.send_text(make_tts_message(self.session_id, "start")):
                return False
            sent_start = True
            if not await self.send_text(
                make_tts_message(self.session_id, "sentence_start", recovery.text)
            ):
                return False
            self.state = SessionState.SPEAKING
            for opus_frame in frames:
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return False
                if not await pacer.wait_for_send(cancel_event):
                    return False
                if not await self.send_binary(pack_audio_payload(opus_frame, self.version)):
                    return False
                sent_audio = True
                pacer.record_frame_sent()
                if pacer.playback_end is not None:
                    self._playback_guard_until = max(
                        self._playback_guard_until,
                        pacer.playback_end,
                    )
            if sent_audio and self._owns_turn(turn_generation) and not cancel_event.is_set():
                await self.send_text(make_tts_message(self.session_id, "stop"))
                self._mark_response_complete(self._playback_guard_until)
                mark_current(
                    "error_fallback_sent",
                    cached=True,
                    provenance=recovery.provenance,
                )
                return True
            return False
        except Exception as exc:
            logger.warning("cached_error_fallback_send_failed session=%s error=%s", self.session_id, exc)
            return False
        finally:
            if sent_start and not sent_audio and self._owns_turn(turn_generation):
                await self.send_text(make_tts_message(self.session_id, "stop"))

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
                cache_result = await self.response_audio_cache.get_or_fill(
                    text,
                    timeout,
                    priority="live",
                )
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
                if final_text:
                    self._mark_conversation_activity("asr_final")
                    self.turn_metrics.record_capture_event(
                        self._capture_generation,
                        "asr_final_accepted",
                        chars=len(final_text),
                    )
                    await self._trigger_ai_turn(
                        final_text,
                        check_end_intent=True,
                        source="asr",
                    )
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

    async def _trigger_ai_turn(
        self,
        transcript: str,
        *,
        check_end_intent: bool = False,
        source: str = "chat",
    ):
        if not transcript.strip():
            return

        if transcript == self.processed_transcript:
            return

        self._cancel_pending_wake()
        self._closing_reason = None
        self._mark_conversation_activity("ai_turn")

        self.processed_transcript = transcript
        logger.info("Triggering AI Turn for prompt: %r", transcript)

        self._abort_turn()

        turn_cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        self.current_cancel_event = turn_cancel_event
        self.current_turn_task = asyncio.create_task(
            self._process_ai_response(
                transcript,
                turn_cancel_event,
                turn_generation,
                check_end_intent=check_end_intent,
                source=source,
            )
        )

    def _confirmation_owner_scope(self) -> str:
        if self._memory_owner_id:
            return f"owner:{self._memory_owner_id}"
        return f"session:{self.session_id}"

    async def _apply_memory_proposal(
        self,
        proposal: Optional[MemoryProposal],
        *,
        turn_id: str,
    ) -> MemoryApplyResult:
        if proposal is None or not self.config.memory.enabled:
            return MemoryApplyResult("ignored")

        action = str(proposal.action or "").strip().lower()
        value = " ".join(str(proposal.value or "").split())
        fact_id = str(proposal.fact_id or "").strip()
        revision = proposal.revision
        evidence = " ".join(str(proposal.evidence or "").split())[:500]

        if action not in {"upsert", "forget", "forget_all"}:
            return MemoryApplyResult("invalid", scope="session")

        def find_session_fact(target_id: str) -> tuple[int, Optional[SessionMemoryFact]]:
            for index, fact in enumerate(self._session_memory):
                if fact.id == target_id:
                    return index, fact
            return -1, None

        def parse_durable_id(target_id: str) -> Optional[int]:
            if not target_id.startswith("durable:"):
                return None
            try:
                parsed = int(target_id.split(":", 1)[1])
            except (TypeError, ValueError):
                return None
            return parsed if parsed > 0 else None

        if action == "upsert" and value:
            if fact_id:
                session_index, session_fact = find_session_fact(fact_id)
                if session_fact is not None:
                    if revision is None or int(revision) != session_fact.revision:
                        return MemoryApplyResult(
                            "revision_conflict",
                            fact_id=fact_id,
                            revision=session_fact.revision,
                            scope="session",
                        )
                    updated = SessionMemoryFact(
                        id=session_fact.id,
                        value=value,
                        revision=session_fact.revision + 1,
                        evidence=evidence,
                    )
                    self._session_memory[session_index] = updated
                    return MemoryApplyResult(
                        "applied",
                        changed=updated.value != session_fact.value,
                        fact_id=updated.id,
                        revision=updated.revision,
                        scope="session",
                    )

                durable_id = parse_durable_id(fact_id)
                if durable_id is None or self._memory_store is None or not self._memory_owner_id:
                    return MemoryApplyResult(
                        "not_found",
                        fact_id=fact_id,
                        revision=revision,
                        scope="personal" if fact_id.startswith("durable:") else "session",
                    )
                if revision is None:
                    return MemoryApplyResult(
                        "revision_required",
                        fact_id=fact_id,
                        scope="personal",
                    )
                updated = await self._memory_store.update_by_id(
                    owner_id=self._memory_owner_id,
                    scope="personal",
                    fact_id=durable_id,
                    expected_revision=int(revision),
                    value=value,
                    source_turn_id=turn_id,
                    evidence=evidence,
                )
                if updated is None:
                    return MemoryApplyResult(
                        "revision_conflict",
                        fact_id=fact_id,
                        revision=revision,
                        scope="personal",
                    )
                return MemoryApplyResult(
                    "applied",
                    changed=True,
                    fact_id=f"durable:{updated.id}",
                    revision=updated.revision,
                    scope="personal",
                )

            # A new memory has no semantic key supplied by the model. Generate
            # an opaque technical identity; future edits/deletes must target
            # the exact ID + revision exposed in context.
            if self._memory_store is not None and self._memory_owner_id:
                key = f"fact_{uuid.uuid4().hex}"
                await self._memory_store.upsert(
                    owner_id=self._memory_owner_id,
                    scope="personal",
                    kind="fact",
                    key=key,
                    value=value,
                    source_turn_id=turn_id,
                    evidence=evidence,
                )
                stored = await self._memory_store.get_active_by_key(
                    owner_id=self._memory_owner_id,
                    scope="personal",
                    key=key,
                )
                if stored is None:
                    return MemoryApplyResult("failed", scope="personal")
                return MemoryApplyResult(
                    "applied",
                    changed=True,
                    fact_id=f"durable:{stored.id}",
                    revision=stored.revision,
                    scope="personal",
                )

            new_fact = SessionMemoryFact(
                id=f"session:{uuid.uuid4().hex}",
                value=value,
                revision=1,
                evidence=evidence,
            )
            self._session_memory.append(new_fact)
            max_items = max(6, self.config.memory.top_k * 2)
            if len(self._session_memory) > max_items:
                del self._session_memory[:-max_items]
            return MemoryApplyResult(
                "applied",
                changed=True,
                fact_id=new_fact.id,
                revision=new_fact.revision,
                scope="session",
            )

        if action == "forget_all":
            session_changed = bool(self._session_memory)
            durable_changed = 0
            if self._memory_store is not None and self._memory_owner_id:
                durable_changed = await self._memory_store.tombstone(
                    owner_id=self._memory_owner_id,
                    scope="personal",
                )
            self._session_memory.clear()
            changed = session_changed or durable_changed > 0
            return MemoryApplyResult(
                "applied" if changed else "not_found",
                changed=changed,
                scope="personal" if durable_changed else "session",
            )

        if action == "forget":
            if not fact_id or revision is None:
                return MemoryApplyResult(
                    "target_required",
                    fact_id=fact_id,
                    revision=revision,
                    scope="session",
                )
            session_index, session_fact = find_session_fact(fact_id)
            if session_fact is not None:
                if int(revision) != session_fact.revision:
                    return MemoryApplyResult(
                        "revision_conflict",
                        fact_id=fact_id,
                        revision=session_fact.revision,
                        scope="session",
                    )
                del self._session_memory[session_index]
                return MemoryApplyResult(
                    "applied",
                    changed=True,
                    fact_id=fact_id,
                    revision=session_fact.revision + 1,
                    scope="session",
                )

            durable_id = parse_durable_id(fact_id)
            if durable_id is None or self._memory_store is None or not self._memory_owner_id:
                return MemoryApplyResult(
                    "not_found",
                    fact_id=fact_id,
                    revision=revision,
                    scope="personal" if fact_id.startswith("durable:") else "session",
                )
            changed = await self._memory_store.tombstone_by_id(
                owner_id=self._memory_owner_id,
                scope="personal",
                fact_id=durable_id,
                expected_revision=int(revision),
            )
            return MemoryApplyResult(
                "applied" if changed else "revision_conflict",
                changed=changed,
                fact_id=fact_id,
                revision=int(revision) + (1 if changed else 0),
                scope="personal",
            )

        return MemoryApplyResult("ignored")

    async def _process_ai_response(
        self,
        user_text: str,
        cancel_event: asyncio.Event,
        turn_generation: int,
        *,
        check_end_intent: bool = False,
        source: str = "chat",
    ):
        if not self._owns_turn(turn_generation):
            return

        self.state = SessionState.THINKING
        t_start = time.perf_counter()
        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )

        trace = self.turn_metrics.start_turn(self._capture_generation, source)
        trace_token = activate_trace(trace)
        stream = None
        producer_task: Optional[asyncio.Task] = None
        completed = False
        close_reason: Optional[str] = None
        current_emotion = "neutral"
        tts_started = False
        first_binary_sent = False
        reply_segments: list[str] = []
        fully_sent_segments: list[str] = []
        active_segment_text: Optional[str] = None
        active_segment_had_audio = False
        tool_calls_seen = 0
        llm_rounds = 1
        action_round_records: list[dict] = []
        # Speech from a round that may still emit tool calls is buffered
        # until terminal validation. Only pure-chat rounds flush; rounds
        # with any action discard first-round speech and rely on the
        # synthesis round so no unvalidated claim reaches TTS.
        round_speech_buffer: list[tuple[str, Optional[str]]] = []
        executed_call_keys: set[tuple[str, str]] = set()
        persona_version = self._persona_snapshot()
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        queue_done = object()
        producer_errors: list[BaseException] = []

        generation_deadline = (
            time.monotonic() + self.config.latency.total_turn_timeout_ms / 1000.0
        )

        def remaining_generation_timeout_ms() -> int:
            remaining = generation_deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("LLM total turn timeout")
            return max(1, int(remaining * 1000.0))

        def _degraded_note() -> str:
            # No literal business renderer: when AI synthesis is unavailable
            # the turn keeps truthful receipts in history and plays only a
            # prewarmed neutral recovery asset (handled by the caller). This
            # note is stored, never spoken as a success claim.
            return (
                "[Receipt synthesis chưa khả dụng; giữ receipt có cấu trúc "
                "trong history, không phát claim thành công.]"
            )

        try:
            self.dialogue.add_user_message(user_text)

            mark_current("context_lookup_start")
            messages = await self.context_builder.build(
                self.dialogue.get_messages_for_llm(),
                query=user_text,
                owner_id=self._memory_owner_id,
                session_memory=self._session_memory,
            )
            # Supply fresh facts for every turn, without classifying user text.
            # AI decides whether these facts are relevant. Never persist this
            # snapshot in dialogue or reuse a previous turn's clock.
            messages.insert(0, clock_context(self.config.server.timezone))
            business_tools = (
                self.tool_registry.openai_tools(limit=self.config.tools.schema_limit)
                if self.config.tools.enabled and self.config.tools.native_enabled
                else []
            )
            pending_action = self.pending_actions.peek()
            if pending_action is not None:
                messages.insert(-1 if messages and messages[-1].get("role") == "user" else len(messages), {
                    "role": "system",
                    "content": (
                        "Pending action hiện tại là dữ liệu trạng thái do server quản lý. "
                        "Hãy hiểu câu người dùng theo ngữ cảnh và dùng veetee_confirmation_decision "
                        "với đúng action_id nếu họ approve/reject/clarify. Nếu họ đổi tham số, gọi tool nghiệp vụ mới.\n"
                        + json.dumps(
                            {
                                "action_id": pending_action.action_id,
                                "tool_name": pending_action.tool_name,
                                "arguments": pending_action.arguments,
                                "expires_in_ms": max(
                                    0,
                                    int((pending_action.expires_at - time.monotonic()) * 1000),
                                ),
                            },
                            ensure_ascii=False,
                            default=str,
                            separators=(",", ":"),
                        )
                    ),
                })
            tools = business_tools + semantic_tools(
                memory_enabled=self.config.memory.enabled,
                pending_action=pending_action is not None,
            )
            detect_end_intent = bool(
                check_end_intent
                and self.config.intent.enabled
                and self.config.intent.semantic_end_enabled
                and self.config.conversation.end_intent_ai_enabled
            )
            messages = self._fit_llm_context(
                messages,
                tools=tools,
                detect_end_intent=detect_end_intent,
            )
            mark_current("context_lookup_end", message_count=len(messages))
            remaining_generation_timeout_ms()

            stream = self.turn_runner.stream(
                messages,
                tools=tools,
                detect_end_intent=detect_end_intent,
                tool_choice=None,
                first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                total_timeout_ms=remaining_generation_timeout_ms(),
            )

            async def produce_events():
                try:
                    async for event in stream:
                        if cancel_event.is_set() or not self._owns_turn(turn_generation):
                            return
                        await event_queue.put(event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    producer_errors.append(exc)
                finally:
                    if not cancel_event.is_set():
                        try:
                            event_queue.put_nowait(queue_done)
                        except asyncio.QueueFull:
                            pass

            producer_task = asyncio.create_task(produce_events())

            async def speak_segment(text: str, emotion: Optional[str] = None):
                nonlocal tts_started, first_binary_sent
                nonlocal active_segment_text, active_segment_had_audio
                cleaned = str(text or "").strip()
                if not cleaned:
                    return
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return

                if not tts_started:
                    emo = emotion or current_emotion or "neutral"
                    if not await self.send_text(make_llm_message(self.session_id, emo, "😊")):
                        raise ConnectionError("failed to send llm status")
                    if not await self.send_text(make_tts_message(self.session_id, "start")):
                        raise ConnectionError("failed to send tts:start")
                    self.state = SessionState.SPEAKING
                    tts_started = True

                if not await self.send_text(
                    make_tts_message(self.session_id, "sentence_start", cleaned)
                ):
                    raise ConnectionError("failed to send sentence_start")

                mark_current("tts_enqueue", chars=len(cleaned), priority="live")
                first_opus_for_segment = True
                segment_audio_sent = False
                active_segment_text = cleaned
                active_segment_had_audio = False
                async for opus_frame in open_tts_stream(
                    self.tts_engine,
                    cleaned,
                    cancel_event,
                    priority="live",
                    queue_deadline_seconds=self.config.latency.total_turn_timeout_ms / 1000.0,
                ):
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        return
                    if first_opus_for_segment:
                        mark_current("tts_first_opus")
                        first_opus_for_segment = False
                    if not await pacer.wait_for_send(cancel_event):
                        return
                    packet = pack_audio_payload(opus_frame, self.version)
                    if not await self.send_binary(packet):
                        raise ConnectionError("failed to send TTS audio")
                    segment_audio_sent = True
                    active_segment_had_audio = True
                    pacer.record_frame_sent()
                    if pacer.playback_end is not None:
                        self._playback_guard_until = max(
                            self._playback_guard_until,
                            pacer.playback_end,
                        )
                    if not first_binary_sent:
                        first_binary_sent = True
                        mark_current("first_ws_binary_sent")
                        logger.info(
                            "Post-ASR first TTS binary sent in %.3fs",
                            time.perf_counter() - t_start,
                        )
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return
                if not segment_audio_sent:
                    raise RuntimeError("TTS produced no audio for speech segment")
                fully_sent_segments.append(cleaned)
                active_segment_text = None
                active_segment_had_audio = False

            while True:
                if producer_task.done() and event_queue.empty():
                    if producer_errors:
                        raise producer_errors[0]
                    break

                event = await event_queue.get()
                if event is queue_done:
                    if producer_errors:
                        raise producer_errors[0]
                    break

                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return

                if isinstance(event, ControlEvent):
                    current_emotion = event.emotion or current_emotion
                    if event.lifecycle == "end" or event.intent == "end_conversation":
                        close_reason = "ai_end_intent"
                        self._cancel_pending_wake()
                        self._invalidate_capture()
                        self.final_transcript_parts.clear()
                        self._speech_active = False
                        self._discard_asr_until_speech_final = False
                        self.processed_transcript = ""
                        self._closing_reason = close_reason
                        logger.info(
                            "conversation_close_requested session=%s reason=%s",
                            self.session_id,
                            close_reason,
                        )
                    continue

                if isinstance(event, SpeechSegmentEvent):
                    if not reply_segments and not round_speech_buffer:
                        logger.info(
                            "Post-ASR first LLM clause ready in %.3fs: %r",
                            time.perf_counter() - t_start,
                            event.text,
                        )
                        mark_current("llm_first_speech_buffered")
                    # Buffer until the round reaches a terminal event. The
                    # flush/discard decision happens after Completed so a
                    # late tool call cannot follow already-spoken speech.
                    round_speech_buffer.append((event.text, event.emotion))
                    continue

                if isinstance(event, MemoryProposalEvent):
                    tool_calls_seen += 1
                    memory_args = {
                        "action": event.action,
                        "value": event.value,
                        "fact_id": event.fact_id,
                        "evidence": event.evidence,
                    }
                    if event.revision is not None:
                        memory_args["revision"] = event.revision
                    try:
                        validate_arguments(self._semantic_memory_schema(), memory_args)
                    except ToolValidationError as exc:
                        applied = MemoryApplyResult("invalid", fact_id=event.fact_id)
                        receipt = make_receipt(
                            call_id=event.call_id, name=MEMORY_TOOL_NAME,
                            arguments=memory_args, status="invalid",
                            turn_id=trace.turn_id, error=str(exc),
                            provenance="memory:validation",
                        )
                        mark_current("memory_proposal_rejected", reason="schema", error=str(exc))
                        action_round_records.append({
                            "call_id": event.call_id,
                            "name": MEMORY_TOOL_NAME,
                            "arguments": memory_args,
                            "receipt": receipt,
                        })
                        continue
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        return
                    proposal = MemoryProposal(
                        action=event.action,
                        value=event.value,
                        fact_id=event.fact_id,
                        revision=event.revision,
                        evidence=event.evidence,
                    )
                    mark_current("memory_write_start", action=event.action)
                    applied = await self._apply_memory_proposal(
                        proposal,
                        turn_id=trace.turn_id,
                    )
                    mark_current(
                        "memory_write_end",
                        action=event.action,
                        applied=applied.applied,
                        status=applied.status,
                        changed=applied.changed,
                        durable=bool(self._memory_owner_id),
                    )
                    receipt = make_receipt(
                        call_id=event.call_id, name=MEMORY_TOOL_NAME,
                        arguments=memory_args, status=applied.status,
                        turn_id=trace.turn_id, changed=applied.changed,
                        data={"fact_id": applied.fact_id, "revision": applied.revision,
                              "scope": applied.scope},
                        provenance=f"memory:{applied.scope}:{applied.fact_id or 'new'}",
                    )
                    action_round_records.append({
                        "call_id": event.call_id,
                        "name": MEMORY_TOOL_NAME,
                        "arguments": memory_args,
                        "receipt": receipt,
                    })
                    continue

                if isinstance(event, ConfirmationDecisionEvent):
                    tool_calls_seen += 1
                    confirmation_args = {
                        "action_id": event.action_id,
                        "decision": event.decision,
                    }
                    try:
                        validate_arguments(self._semantic_confirmation_schema(), confirmation_args)
                    except ToolValidationError as exc:
                        receipt = make_receipt(
                            call_id=event.call_id, name=CONFIRMATION_TOOL_NAME,
                            arguments=confirmation_args, status="invalid",
                            turn_id=trace.turn_id, error=str(exc),
                            provenance="confirmation:validation",
                        )
                        mark_current("confirmation_rejected", reason="schema", error=str(exc))
                        action_round_records.append({
                            "call_id": event.call_id,
                            "name": CONFIRMATION_TOOL_NAME,
                            "arguments": confirmation_args,
                            "receipt": receipt,
                        })
                        continue
                    resolution, pending = self.pending_actions.resolve(
                        action_id=event.action_id,
                        decision=event.decision,
                        session_id=self.session_id,
                        owner_scope=self._confirmation_owner_scope(),
                    )
                    receipt = make_receipt(
                        call_id=event.call_id, name=CONFIRMATION_TOOL_NAME,
                        arguments=confirmation_args, status=resolution,
                        turn_id=trace.turn_id,
                        data={"action_id": event.action_id},
                        provenance="confirmation:decision",
                    )
                    if resolution == "approve" and pending is not None:
                        # Re-validate ownership/cancel/deadline immediately
                        # before dispatch; approval never implies execution.
                        if cancel_event.is_set() or not self._owns_turn(turn_generation):
                            return
                        try:
                            remaining_generation_timeout_ms()
                        except asyncio.TimeoutError:
                            result = ToolResult(
                                pending.action_id, pending.tool_name,
                                ToolStatus.TIMED_OUT, error="generation deadline before dispatch",
                            )
                        else:
                            descriptor = self.tool_registry.get(pending.tool_name)
                            if descriptor is None:
                                result = ToolResult(
                                    pending.action_id,
                                    pending.tool_name,
                                    ToolStatus.FAILED,
                                    error="tool is no longer available",
                                )
                            else:
                                try:
                                    validate_arguments(descriptor.input_schema, pending.arguments)
                                except ToolValidationError as exc:
                                    result = ToolResult(
                                        pending.action_id,
                                        pending.tool_name,
                                        ToolStatus.FAILED,
                                        error=str(exc),
                                    )
                                else:
                                    mark_current("tool_execute_start", tool=pending.tool_name, confirmed=True)
                                    result = await self.tool_executor.execute(
                                        pending.action_id,
                                        pending.tool_name,
                                        pending.arguments,
                                        turn_id=pending.turn_id,
                                        cancel_event=cancel_event,
                                    )
                                    mark_current(
                                        "tool_execute_end",
                                        tool=pending.tool_name,
                                        status=result.status.value,
                                        confirmed=True,
                                    )
                        receipt = make_receipt(
                            call_id=event.call_id, name=CONFIRMATION_TOOL_NAME,
                            arguments=confirmation_args, status=resolution,
                            turn_id=trace.turn_id,
                            data={"action_id": event.action_id, "tool_name": pending.tool_name,
                                  "pending_retained": False},
                            execution={"status": result.status.value, "data": result.data,
                                       "error": result.error},
                            provenance="confirmation:execution",
                        )
                    elif pending is not None:
                        receipt = make_receipt(
                            call_id=event.call_id, name=CONFIRMATION_TOOL_NAME,
                            arguments=confirmation_args, status=resolution,
                            turn_id=trace.turn_id,
                            data={"action_id": event.action_id, "tool_name": pending.tool_name,
                                  "pending_retained": resolution == "clarify"},
                            provenance="confirmation:decision",
                        )
                    mark_current(
                        "confirmation_decision_resolved",
                        action_id=event.action_id,
                        decision=event.decision,
                        status=resolution,
                    )
                    action_round_records.append({
                        "call_id": event.call_id,
                        "name": CONFIRMATION_TOOL_NAME,
                        "arguments": {
                            "action_id": event.action_id,
                            "decision": event.decision,
                        },
                        "receipt": receipt,
                    })
                    continue

                if isinstance(event, ToolCallReadyEvent):
                    tool_calls_seen += 1
                    # Loop detection is structural: same origin turn/call with
                    # different args, or a repeated fingerprint after dispatch,
                    # never user-text patterns.
                    call_key = (trace.turn_id, event.call_id)
                    if call_key in executed_call_keys:
                        mark_current("tool_call_rejected", reason="duplicate_call_id")
                        receipt = make_receipt(
                            call_id=event.call_id, name=event.name,
                            arguments=event.arguments, status="failed",
                            turn_id=trace.turn_id,
                            error="duplicate tool call id in one turn",
                            provenance=f"tool:{event.name}",
                        )
                        action_round_records.append({
                            "call_id": event.call_id,
                            "name": event.name,
                            "arguments": event.arguments,
                            "receipt": receipt,
                        })
                        continue
                    executed_call_keys.add(call_key)
                    if tool_calls_seen > self.config.tools.max_calls_per_turn:
                        mark_current("tool_call_rejected", reason="turn_limit")
                        result = ToolResult(
                            event.call_id,
                            event.name,
                            ToolStatus.FAILED,
                            error="turn tool limit exceeded",
                        )
                        receipt = make_receipt(
                            call_id=event.call_id, name=event.name,
                            arguments=event.arguments, status=result.status.value,
                            turn_id=trace.turn_id, data=result.data, error=result.error,
                            provenance=f"tool:{event.name}",
                        )
                    else:
                        descriptor = self.tool_registry.get(event.name)
                        if descriptor is None:
                            # MCP/capability gate: unknown tools fail closed.
                            # Read-only markers in descriptions are never
                            # trusted for execution safety.
                            result = ToolResult(
                                event.call_id,
                                event.name,
                                ToolStatus.FAILED,
                                error="unknown tool",
                            )
                            receipt = make_receipt(
                                call_id=event.call_id, name=event.name,
                                arguments=event.arguments, status=result.status.value,
                                turn_id=trace.turn_id, data=result.data, error=result.error,
                                provenance=f"tool:{event.name}",
                            )
                        else:
                            try:
                                validate_arguments(descriptor.input_schema, event.arguments)
                            except ToolValidationError as exc:
                                result = ToolResult(
                                    event.call_id,
                                    event.name,
                                    ToolStatus.FAILED,
                                    error=str(exc),
                                )
                                receipt = make_receipt(
                                    call_id=event.call_id, name=event.name,
                                    arguments=event.arguments, status=result.status.value,
                                    turn_id=trace.turn_id, data=result.data, error=result.error,
                                    provenance=f"tool:{event.name}",
                                )
                            else:
                                requires_confirmation = (
                                    descriptor.requires_confirmation and not descriptor.read_only
                                )
                                if requires_confirmation:
                                    pending = self.pending_actions.prepare(
                                        action_id=event.call_id,
                                        turn_id=trace.turn_id,
                                        tool_name=event.name,
                                        arguments=event.arguments,
                                        session_id=self.session_id,
                                        owner_scope=self._confirmation_owner_scope(),
                                        ttl_seconds=self.config.intent.confirmation_ttl_seconds,
                                    )
                                    mark_current(
                                        "tool_waiting_confirmation",
                                        tool=event.name,
                                        action_id=event.call_id,
                                    )
                                    # Awaiting confirmation is distinct from
                                    # approved and from execution succeeded.
                                    receipt = make_receipt(
                                        call_id=event.call_id, name=event.name,
                                        arguments=event.arguments, status="confirmation_required",
                                        turn_id=trace.turn_id,
                                        data={"action_id": pending.action_id,
                                              "expires_in_ms": max(
                                                  0, int((pending.expires_at - time.monotonic()) * 1000),
                                              )},
                                        provenance="confirmation:required",
                                    )
                                    action_round_records.append({
                                        "call_id": event.call_id,
                                        "name": event.name,
                                        "arguments": event.arguments,
                                        "receipt": receipt,
                                    })
                                    continue

                                # Ownership/cancel/deadline recheck right
                                # before dispatch. Queued work cancelled
                                # before dispatch never runs.
                                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                                    return
                                try:
                                    remaining_generation_timeout_ms()
                                except asyncio.TimeoutError:
                                    result = ToolResult(
                                        event.call_id, event.name, ToolStatus.TIMED_OUT,
                                        error="generation deadline before dispatch",
                                    )
                                else:
                                    mark_current("tool_execute_start", tool=event.name)
                                    result = await self.tool_executor.execute(
                                        event.call_id,
                                        event.name,
                                        event.arguments,
                                        turn_id=trace.turn_id,
                                        cancel_event=cancel_event,
                                    )
                                    mark_current(
                                        "tool_execute_end",
                                        tool=event.name,
                                        status=result.status.value,
                                    )
                                # unknown stays unknown; never coerced.
                                receipt = make_receipt(
                                    call_id=event.call_id, name=event.name,
                                    arguments=event.arguments, status=result.status.value,
                                    turn_id=trace.turn_id, data=result.data, error=result.error,
                                    provenance=f"tool:{event.name}",
                                )
                    action_round_records.append({
                        "call_id": event.call_id,
                        "name": event.name,
                        "arguments": event.arguments,
                        "receipt": receipt,
                    })
                    continue

                if isinstance(event, FailedEvent):
                    raise RuntimeError(event.error)

                if isinstance(event, CompletedEvent):
                    completed = True
                    continue

            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                return
            if not completed:
                raise RuntimeError("LLM turn ended without CompletedEvent")
            # Persona snapshot must stay consistent across rounds of one turn.
            if self._persona_snapshot() != persona_version:
                mark_current("persona_changed_mid_turn", before=persona_version,
                             after=self._persona_snapshot())

            if not action_round_records:
                # Pure chat: terminal validation passed, flush buffered speech.
                for text, emotion in round_speech_buffer:
                    reply_segments.append(text)
                    await speak_segment(text, emotion)
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        return
                mark_current("speech_flushed_after_terminal", segments=len(round_speech_buffer))
                round_speech_buffer.clear()
            else:
                # Any action discards first-round speech: it was produced
                # before receipts existed and must never reach TTS.
                if round_speech_buffer:
                    mark_current("speech_discarded_for_action_round",
                                 segments=len(round_speech_buffer),
                                 actions=len(action_round_records))
                    round_speech_buffer.clear()

            if (
                action_round_records
                and self.config.tools.tool_result_synthesis
                and self.config.tools.max_llm_rounds_per_turn >= 2
                and not cancel_event.is_set()
                and self._owns_turn(turn_generation)
            ):
                llm_rounds = 2
                assistant_tool_calls = []
                for record in action_round_records:
                    assistant_tool_calls.append({
                        "id": record["call_id"],
                        "type": "function",
                        "function": {
                            "name": record["name"],
                            "arguments": json.dumps(
                                record["arguments"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    })

                synthesis_messages = list(messages)
                synthesis_messages.append({
                    "role": "assistant",
                    "content": " ".join(reply_segments).strip() or None,
                    "tool_calls": assistant_tool_calls,
                })
                for record in action_round_records:
                    synthesis_messages.append({
                        "role": "tool",
                        "tool_call_id": record["call_id"],
                        "name": record["name"],
                        "content": json.dumps(
                            record["receipt"],
                            ensure_ascii=False,
                            default=str,
                            separators=(",", ":"),
                        ),
                    })

                # Bounded agent loop: AI may chain A->receipt->B before the
                # final speech. Chat-only turns never enter here. Every
                # receipt is phrased by the model; no literal renderer.
                synthesized_segments = 0
                max_rounds = max(2, int(self.config.tools.max_llm_rounds_per_turn))
                synthesized_round = 1
                pending_receipt_index = 0
                synthesis_failed = False
                synthesis_completed = True
                while pending_receipt_index < len(action_round_records) or synthesized_round == 1:
                    if synthesized_round >= max_rounds and pending_receipt_index < len(action_round_records):
                        # Budget exhausted with unphrased receipts: keep truth.
                        mark_current("action_receipt_budget_exhausted",
                                     rounds=synthesized_round, records=len(action_round_records))
                        synthesis_failed = True
                        break
                    synthesized_round += 1
                    llm_rounds = synthesized_round
                    # Intermediate rounds (max>=3 and not final) may emit
                    # follow-up tools for A->B chains; the final round is
                    # speech-only. AI decides clarify/end in any round.
                    allow_follow_tools = (
                        synthesized_round < max_rounds and max_rounds >= 3
                    )
                    assistant_tool_calls = []
                    for record in action_round_records:
                        assistant_tool_calls.append({
                            "id": record["call_id"],
                            "type": "function",
                            "function": {
                                "name": record["name"],
                                "arguments": json.dumps(
                                    record["arguments"],
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            },
                        })
                    round_messages = list(messages)
                    round_messages.append({
                        "role": "assistant",
                        "content": " ".join(reply_segments).strip() or None,
                        "tool_calls": assistant_tool_calls,
                    })
                    for record in action_round_records:
                        round_messages.append({
                            "role": "tool",
                            "tool_call_id": record["call_id"],
                            "name": record["name"],
                            "content": json.dumps(
                                record["receipt"],
                                ensure_ascii=False,
                                default=str,
                                separators=(",", ":"),
                            ),
                        })
                    # Freshness: clock data is only valid for this turn.
                    # Past assistant time strings in history are never a
                    # substitute for the current receipt.
                    try:
                        fitted_round = self._fit_llm_context(
                            round_messages,
                            tools=(business_tools + semantic_tools(
                                memory_enabled=self.config.memory.enabled,
                                pending_action=self.pending_actions.peek() is not None,
                            )) if allow_follow_tools else [],
                            detect_end_intent=bool(detect_end_intent),
                        )
                        mark_current("llm_round_start", round=synthesized_round,
                                     purpose="action_receipt_synthesis",
                                     allow_tools=allow_follow_tools)
                        round_speech: list[tuple[str, Optional[str]]] = []
                        round_tools: list[ToolCallReadyEvent] = []
                        round_memory: list[MemoryProposalEvent] = []
                        round_confirm: list[ConfirmationDecisionEvent] = []
                        round_completed = False
                        round_failed = False
                        async for event in self.turn_runner.stream(
                            fitted_round,
                            tools=(business_tools + semantic_tools(
                                memory_enabled=self.config.memory.enabled,
                                pending_action=self.pending_actions.peek() is not None,
                            )) if allow_follow_tools else [],
                            detect_end_intent=bool(detect_end_intent),
                            tool_choice=None if allow_follow_tools else "none",
                            first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                            total_timeout_ms=remaining_generation_timeout_ms(),
                        ):
                            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                                return
                            if isinstance(event, ControlEvent):
                                current_emotion = event.emotion or current_emotion
                                if event.lifecycle == "end" or event.intent == "end_conversation":
                                    close_reason = "ai_end_intent"
                                    self._closing_reason = close_reason
                                continue
                            if isinstance(event, SpeechSegmentEvent):
                                if allow_follow_tools:
                                    round_speech.append((event.text, event.emotion))
                                else:
                                    synthesized_segments += 1
                                    reply_segments.append(event.text)
                                    await speak_segment(event.text, event.emotion)
                                continue
                            if isinstance(event, MemoryProposalEvent):
                                if allow_follow_tools:
                                    round_memory.append(event)
                                else:
                                    mark_current("memory_proposal_rejected", reason="final_round")
                                    round_failed = True
                                    break
                                continue
                            if isinstance(event, ConfirmationDecisionEvent):
                                if allow_follow_tools:
                                    round_confirm.append(event)
                                else:
                                    mark_current("confirmation_rejected", reason="final_round")
                                    round_failed = True
                                    break
                                continue
                            if isinstance(event, ToolCallReadyEvent):
                                if allow_follow_tools:
                                    round_tools.append(event)
                                else:
                                    mark_current("tool_call_rejected", reason="final_round")
                                    round_failed = True
                                    break
                                continue
                            if isinstance(event, FailedEvent):
                                round_failed = True
                                break
                            if isinstance(event, CompletedEvent):
                                round_completed = True
                        mark_current("llm_round_end", round=synthesized_round,
                                     completed=round_completed, failed=round_failed,
                                     speech=len(round_speech), tools=len(round_tools))
                        if round_failed or not round_completed:
                            synthesis_failed = True
                            synthesis_completed = False
                            break
                        # Execute follow-up tools from this synthesis round.
                        # Independent read-only tools overlap; writes and
                        # dependent calls keep order via executor groups.
                        new_records: list[dict] = []
                        if round_tools or round_memory or round_confirm:
                            # Discard intermediate speech when new actions
                            # exist; the next round rephrases with receipts.
                            if round_speech:
                                mark_current("intermediate_speech_discarded",
                                             segments=len(round_speech))
                            for tool_event in round_tools:
                                tool_calls_seen += 1
                                if tool_calls_seen > self.config.tools.max_calls_per_turn:
                                    receipt = make_receipt(
                                        call_id=tool_event.call_id, name=tool_event.name,
                                        arguments=tool_event.arguments, status="failed",
                                        turn_id=trace.turn_id, error="turn tool limit exceeded",
                                        provenance=f"tool:{tool_event.name}",
                                    )
                                    new_records.append({"call_id": tool_event.call_id,
                                                        "name": tool_event.name,
                                                        "arguments": tool_event.arguments,
                                                        "receipt": receipt})
                                    continue
                                descriptor = self.tool_registry.get(tool_event.name)
                                if descriptor is None:
                                    receipt = make_receipt(
                                        call_id=tool_event.call_id, name=tool_event.name,
                                        arguments=tool_event.arguments, status="failed",
                                        turn_id=trace.turn_id, error="unknown tool",
                                        provenance=f"tool:{tool_event.name}",
                                    )
                                    new_records.append({"call_id": tool_event.call_id,
                                                        "name": tool_event.name,
                                                        "arguments": tool_event.arguments,
                                                        "receipt": receipt})
                                    continue
                                try:
                                    validate_arguments(descriptor.input_schema, tool_event.arguments)
                                except ToolValidationError as exc:
                                    receipt = make_receipt(
                                        call_id=tool_event.call_id, name=tool_event.name,
                                        arguments=tool_event.arguments, status="failed",
                                        turn_id=trace.turn_id, error=str(exc),
                                        provenance=f"tool:{tool_event.name}",
                                    )
                                    new_records.append({"call_id": tool_event.call_id,
                                                        "name": tool_event.name,
                                                        "arguments": tool_event.arguments,
                                                        "receipt": receipt})
                                    continue
                                if descriptor.requires_confirmation and not descriptor.read_only:
                                    pending = self.pending_actions.prepare(
                                        action_id=tool_event.call_id, turn_id=trace.turn_id,
                                        tool_name=tool_event.name, arguments=tool_event.arguments,
                                        session_id=self.session_id,
                                        owner_scope=self._confirmation_owner_scope(),
                                        ttl_seconds=self.config.intent.confirmation_ttl_seconds,
                                    )
                                    receipt = make_receipt(
                                        call_id=tool_event.call_id, name=tool_event.name,
                                        arguments=tool_event.arguments, status="confirmation_required",
                                        turn_id=trace.turn_id,
                                        data={"action_id": pending.action_id},
                                        provenance="confirmation:required",
                                    )
                                    new_records.append({"call_id": tool_event.call_id,
                                                        "name": tool_event.name,
                                                        "arguments": tool_event.arguments,
                                                        "receipt": receipt})
                                    continue
                                # Overlap independent reads.
                                new_records.append({"_deferred_tool": tool_event, "_descriptor": descriptor})
                            # Resolve deferred reads concurrently.
                            deferred = [r for r in new_records if "_deferred_tool" in r]
                            if deferred:
                                async def _run_one(item: dict) -> dict:
                                    tev: ToolCallReadyEvent = item["_deferred_tool"]
                                    if cancel_event.is_set():
                                        return {"call_id": tev.call_id, "name": tev.name,
                                                "arguments": tev.arguments,
                                                "receipt": make_receipt(
                                                    call_id=tev.call_id, name=tev.name,
                                                    arguments=tev.arguments, status="cancelled",
                                                    turn_id=trace.turn_id, error="turn cancelled",
                                                    provenance=f"tool:{tev.name}")}
                                    started = time.perf_counter()
                                    try:
                                        result = await self.tool_executor.execute(
                                            tev.call_id, tev.name, tev.arguments,
                                            turn_id=trace.turn_id, cancel_event=cancel_event,
                                        )
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as exc:
                                        result = ToolResult(tev.call_id, tev.name,
                                                            ToolStatus.FAILED, error=str(exc))
                                    _ = time.perf_counter() - started
                                    return {"call_id": tev.call_id, "name": tev.name,
                                            "arguments": tev.arguments,
                                            "receipt": make_receipt(
                                                call_id=tev.call_id, name=tev.name,
                                                arguments=tev.arguments, status=result.status.value,
                                                turn_id=trace.turn_id, data=result.data,
                                                error=result.error,
                                                provenance=f"tool:{tev.name}")}
                                # Reads overlap up to configured parallelism;
                                # executor group locks still serialize writes.
                                resolved = await asyncio.gather(*(_run_one(item) for item in deferred))
                                resolved_map = {r["call_id"]: r for r in resolved}
                                new_records = [
                                    resolved_map[r["_deferred_tool"].call_id]
                                    if "_deferred_tool" in r else r
                                    for r in new_records
                                ]
                            # Memory/confirmation follow-ups are applied in order.
                            for mem_event in round_memory:
                                tool_calls_seen += 1
                                margs = {"action": mem_event.action, "value": mem_event.value,
                                         "fact_id": mem_event.fact_id,
                                         "evidence": mem_event.evidence}
                                if mem_event.revision is not None:
                                    margs["revision"] = mem_event.revision
                                try:
                                    validate_arguments(self._semantic_memory_schema(), margs)
                                except ToolValidationError as exc:
                                    new_records.append({"call_id": mem_event.call_id,
                                                        "name": MEMORY_TOOL_NAME, "arguments": margs,
                                                        "receipt": make_receipt(
                                                            call_id=mem_event.call_id, name=MEMORY_TOOL_NAME,
                                                            arguments=margs, status="invalid",
                                                            turn_id=trace.turn_id, error=str(exc),
                                                            provenance="memory:validation")})
                                    continue
                                applied = await self._apply_memory_proposal(
                                    MemoryProposal(action=mem_event.action, value=mem_event.value,
                                                   fact_id=mem_event.fact_id, revision=mem_event.revision,
                                                   evidence=mem_event.evidence),
                                    turn_id=trace.turn_id)
                                new_records.append({"call_id": mem_event.call_id,
                                                    "name": MEMORY_TOOL_NAME, "arguments": margs,
                                                    "receipt": make_receipt(
                                                        call_id=mem_event.call_id, name=MEMORY_TOOL_NAME,
                                                        arguments=margs, status=applied.status,
                                                        turn_id=trace.turn_id, changed=applied.changed,
                                                        data={"fact_id": applied.fact_id,
                                                              "revision": applied.revision,
                                                              "scope": applied.scope},
                                                        provenance="memory:followup")})
                            for conf_event in round_confirm:
                                tool_calls_seen += 1
                                cargs = {"action_id": conf_event.action_id, "decision": conf_event.decision}
                                resolution, pending = self.pending_actions.resolve(
                                    action_id=conf_event.action_id, decision=conf_event.decision,
                                    session_id=self.session_id,
                                    owner_scope=self._confirmation_owner_scope())
                                new_records.append({"call_id": conf_event.call_id,
                                                    "name": CONFIRMATION_TOOL_NAME, "arguments": cargs,
                                                    "receipt": make_receipt(
                                                        call_id=conf_event.call_id,
                                                        name=CONFIRMATION_TOOL_NAME, arguments=cargs,
                                                        status=resolution, turn_id=trace.turn_id,
                                                        data={"action_id": conf_event.action_id},
                                                        provenance="confirmation:followup")})
                            if new_records:
                                action_round_records.extend(new_records)
                                pending_receipt_index = len(action_round_records) - len(new_records)
                                # Need another round to phrase the new receipts.
                                if synthesized_round >= max_rounds:
                                    mark_current("action_receipt_budget_exhausted",
                                                 rounds=synthesized_round)
                                    synthesis_failed = True
                                    break
                                continue
                            # No new actions: flush intermediate speech if any.
                            for text, emotion in round_speech:
                                synthesized_segments += 1
                                reply_segments.append(text)
                                await speak_segment(text, emotion)
                            pending_receipt_index = len(action_round_records)
                            break
                        else:
                            # A speech-only follow-up already answered. Do not
                            # call the model again just because old receipts
                            # remain in the list. For reasoning rounds, commit
                            # buffered speech only after terminal validation.
                            for text, emotion in round_speech:
                                synthesized_segments += 1
                                reply_segments.append(text)
                                await speak_segment(text, emotion)
                            pending_receipt_index = len(action_round_records)
                            break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        synthesis_failed = True
                        synthesis_completed = False
                        logger.warning("Action receipt synthesis round failed: %s", exc)
                        break
                    if synthesized_round >= 6:
                        break

                if synthesis_failed or synthesized_segments == 0:
                    mark_current("action_receipt_synthesis_unavailable",
                                 record_count=len(action_round_records))
                    if synthesized_segments == 0:
                        # No literal business fallback: keep receipts in
                        # history and do not invent a success claim.
                        self.dialogue.add_system_message(_degraded_note(), turn_id=trace.turn_id)
                        mark_current("action_receipt_degraded_no_literal",
                                     record_count=len(action_round_records))
                    else:
                        mark_current("action_receipt_partial_kept", segments=synthesized_segments)

            if close_reason and tts_started:
                drain_seconds = pacer.estimated_lead_ms() / 1000.0
                drain_seconds += self.config.conversation.close_grace_ms / 1000.0
                logger.info(
                    "conversation_close_drain session=%s reason=%s estimated_ms=%.0f",
                    self.session_id,
                    close_reason,
                    drain_seconds * 1000.0,
                )
                if not await self._wait_cancelable(drain_seconds, cancel_event):
                    return

            if tts_started:
                if not await self.send_text(make_tts_message(self.session_id, "stop")):
                    raise ConnectionError("failed to send tts:stop")
                logger.info(
                    "Post-ASR tts:stop sent in %.3fs",
                    time.perf_counter() - t_start,
                )

            complete_text = " ".join(reply_segments).strip()
            structured_receipts = [dict(record.get("receipt") or {}) for record in action_round_records]
            if complete_text:
                self.dialogue.add_assistant_message(
                    complete_text, playback="sent" if first_binary_sent else "generated",
                    turn_id=trace.turn_id,
                )
            if structured_receipts:
                # Structured receipt history survives into the next turn so
                # follow-ups reconcile dispatch/cancel outcomes with provenance.
                self.dialogue.add_tool_receipts(structured_receipts, turn_id=trace.turn_id)
            self.dialogue.record_structured_turn(
                turn_id=trace.turn_id, user_text=user_text,
                assistant_text=complete_text, receipts=structured_receipts,
                playback="sent" if first_binary_sent else ("generated" if complete_text else "unknown"),
            )

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
            trace.finish(
                "completed",
                audio_sent=first_binary_sent,
                tool_calls=tool_calls_seen,
                llm_rounds=llm_rounds,
            )

            if close_reason:
                await self._close_transport_and_session(close_reason)
                return

            self.state = (
                SessionState.IDLE
                if self.listening_mode == "manual"
                else SessionState.LISTENING
            )
            if not (
                self.listening_mode == "realtime"
                and self._discard_asr_until_speech_final
            ):
                self._discard_asr_until_speech_final = False
            self._speech_active = False
            self.final_transcript_parts.clear()
            self._mark_response_complete(self._playback_guard_until)

        except asyncio.CancelledError:
            logger.info("Response task cancelled")
            if first_binary_sent:
                sent_text = " ".join(fully_sent_segments).strip()
                details = []
                if sent_text:
                    details.append(f"server đã gửi trọn các đoạn: {sent_text}")
                if active_segment_had_audio and active_segment_text:
                    details.append(f"đoạn đang phát: {active_segment_text}")
                detail_text = "; ".join(details)
                interrupted_note = "[Phản hồi bị ngắt khi đang phát"
                if detail_text:
                    interrupted_note += f"; {detail_text}"
                interrupted_note += "; không xác định người dùng đã nghe được bao nhiêu.]"
                self.dialogue.add_assistant_message(interrupted_note)
            if trace.outcome == "running":
                trace.finish("cancelled")
            return
        except Exception as exc:
            logger.error("Error during AI response processing: %s", exc, exc_info=True)
            if trace.outcome == "running":
                trace.finish("failed", error=type(exc).__name__)
            if self._owns_turn(turn_generation):
                if tts_started:
                    await self.send_text(make_tts_message(self.session_id, "stop"))
                if not first_binary_sent:
                    await self._send_cached_error_fallback(cancel_event, turn_generation)
                self.state = (
                    SessionState.IDLE
                    if self.listening_mode == "manual"
                    else SessionState.LISTENING
                )
                # A failed turn must not suppress the same utterance on the
                # next genuine capture.
                self.processed_transcript = ""
                self._discard_asr_until_speech_final = False
                self._speech_active = False
                self.final_transcript_parts.clear()
        finally:
            if producer_task is not None and not producer_task.done():
                producer_task.cancel()
            if producer_task is not None:
                try:
                    await producer_task
                except asyncio.CancelledError:
                    pass
            if stream is not None:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except RuntimeError:
                        pass
            reset_trace(trace_token)
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

    async def _send_mcp_payload(self, payload: Dict[str, Any]) -> bool:
        message = {
            "session_id": self.session_id,
            "type": MessageType.MCP,
            "payload": payload,
        }
        return await self.send_text(
            json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        )

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

        if self.mcp_device is not None:
            await self.mcp_device.close()

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
