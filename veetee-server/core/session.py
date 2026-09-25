import asyncio
import hashlib
import json
import logging
import uuid
import time
from dataclasses import dataclass, field
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
from core.context_builder import ContextBuilder, MemoryContextPrefetch
from core.clock_context import clock_context
from core.intent import PendingActionStore
from core.ai_contract import (
    CONFIRMATION_TOOL_NAME,
    IDLE_FAREWELL_RETRY_PROMPT,
    IDLE_FAREWELL_SYSTEM_PROMPT,
    IDLE_FAREWELL_USER_PROMPT,
    MEMORY_TOOL_NAME,
    NO_ACTION_TOOL_NAME,
    SEMANTIC_SYSTEM_PROMPT,
    semantic_tools,
)
from core.receipts import make_receipt
from core.memory.models import MemoryApplyResult, MemoryProposal, SessionMemoryFact
from core.memory.retrieval import MemoryRetriever
from core.memory.store import MemoryStore
from core.tools.builtin.calculator import calculator_descriptor
from core.tools.builtin.music_tool import MusicToolProvider
from core.music_player import MusicPlayer
from core.playback import PlaybackCoordinator, PlaybackLease
from core.session_lifecycle import SessionLifecycleState
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
from core.turn_metrics import (
    TurnMetricsRecorder,
    TurnTraceStore,
    activate_trace,
    mark_current,
    normalize_llm_usage,
    reset_trace,
)
from core.turn_runner import TurnRunner
from config.settings import AppConfig

logger = logging.getLogger("ClientSession")


_SPECULATIVE_LLM_DONE = object()


class _ExpectedTurnFailure(RuntimeError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = str(code or "expected_failure")


@dataclass
class _SpeculativeLLMHandle:
    text: str
    capture_generation: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    metadata_ready: asyncio.Event = field(default_factory=asyncio.Event)
    metadata: Optional[dict[str, Any]] = None
    error: Optional[BaseException] = None
    task: Optional[asyncio.Task] = None
    tts_prefetch_text: str = ""
    tts_prefetch_queue: Optional[asyncio.Queue] = None
    tts_prefetch_done: asyncio.Event = field(default_factory=asyncio.Event)
    tts_prefetch_cancel_event: Optional[asyncio.Event] = None
    tts_prefetch_error: Optional[BaseException] = None
    tts_prefetch_task: Optional[asyncio.Task] = None
    tts_prefetch_consumed: bool = False


def _normalize_farewell_text(text: str) -> str:
    return " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or "")).split()
    )


def _is_valid_idle_farewell(text: str, previous_reply: str = "") -> bool:
    """Validate structural safety/anti-repeat only, never farewell semantics.

    Whether the text actually expresses a goodbye belongs to the LLM prompt.
    Server code only prevents malformed output and replay of the prior answer.
    """
    cleaned = (text or "").strip()
    if not cleaned or len(cleaned) > 180 or "\n" in cleaned:
        return False
    candidate = _normalize_farewell_text(cleaned)
    previous = _normalize_farewell_text(previous_reply)
    if previous and candidate and (candidate == previous or previous in candidate):
        return False
    return True


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
        recovery_audio_cache: Optional[ResponseAudioCache] = None,
        turn_trace_store: Optional[TurnTraceStore] = None,
        authenticated_owner_id: Optional[str] = None,
    ):
        self.websocket = websocket
        self.config = app_config
        self.tts_engine = tts_engine
        self.llm_engine = llm_engine
        self.response_audio_cache = response_audio_cache
        # Emergency recovery is system-level and prewarmed once at startup.
        # Keep it separate from assistant-specific fixed-response caching so a
        # custom assistant can recover from LLM/TTS control-plane failures
        # without forcing its normal speech through the default voice cache.
        self.recovery_audio_cache = recovery_audio_cache or response_audio_cache
        
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
        self.output_audio_format = "opus"
        
        self.state = SessionState.IDLE
        self.codec = AudioCodec(
            in_sample_rate=16000,
            out_sample_rate=app_config.tts.sample_rate,
            frame_duration_ms=app_config.tts.frame_duration_ms
        )
        
        self.dialogue = DialogueContext(
            max_history_turns=self.config.conversation.history_turns
        )
        self.turn_runner = TurnRunner(self.llm_engine)
        self._session_memory: list[SessionMemoryFact] = []
        self._memory_store: Optional[MemoryStore] = None
        self._memory_owner_id: Optional[str] = None
        retriever = None
        if self.config.memory.enabled and self.config.memory.durable_enabled:
            owner_id = (authenticated_owner_id or "").strip()
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
            # Local date/time is always available through authoritative
            # server_clock. Do not expose a time tool on every turn: models
            # can otherwise waste a tool round or misuse it for local time.
            descriptors = [calculator_descriptor()]
        self.playback = PlaybackCoordinator()
        self.music_player = MusicPlayer(
            send_text=self.send_text,
            send_binary=self.send_binary,
            session_id=self.session_id,
            version=lambda: self.version,
            stall_timeout_s=self.config.music.stall_timeout_s,
            send_ahead_ms=self.config.music.send_ahead_ms,
            pack_audio=self._pack_tts_audio,
            playback=self.playback,
        )
        self._music_ducked = False
        self._music_interruption_gate = False
        self.music_tools = None
        if self.config.tools.enabled and self.config.music.enabled:
            self.music_tools = MusicToolProvider(
                self.music_player,
                search_results=self.config.music.search_results,
                resolve_timeout_s=self.config.music.resolve_timeout_s,
            )
            descriptors = descriptors + self.music_tools.descriptors()
        self.tool_registry = ToolRegistry(descriptors)
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            max_calls_per_turn=self.config.tools.max_calls_per_turn,
            max_parallel_read_only=self.config.tools.max_parallel_read_only,
        )
        self.pending_actions = PendingActionStore()
        self.mcp_device: Optional[MCPDeviceClient] = None
        if self.config.tools.enabled and self.config.tools.mcp_device_enabled:
            self.mcp_device = MCPDeviceClient(
                send_payload=self._send_mcp_payload,
                registry=self.tool_registry,
            )
        self.lifecycle = SessionLifecycleState()
        self._speculative_llm_task: Optional[asyncio.Task] = None
        self._speculative_llm_handle: Optional[_SpeculativeLLMHandle] = None
        self._speculative_llm_text = ""
        self._speculative_llm_capture_generation: Optional[int] = None
        self._history_summary_task: Optional[asyncio.Task] = None
        self._playback_guard_until = 0.0
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

    # Compatibility properties keep the historical ClientSession surface
    # while lifecycle ownership lives in SessionLifecycleState.
    @property
    def current_turn_task(self) -> Optional[asyncio.Task]:
        return self.lifecycle.current_turn_task

    @current_turn_task.setter
    def current_turn_task(self, task: Optional[asyncio.Task]) -> None:
        self.lifecycle.current_turn_task = task

    @property
    def current_cancel_event(self) -> Optional[asyncio.Event]:
        return self.lifecycle.current_cancel_event

    @current_cancel_event.setter
    def current_cancel_event(self, event: Optional[asyncio.Event]) -> None:
        self.lifecycle.current_cancel_event = event

    @property
    def is_active(self) -> bool:
        return self.lifecycle.active

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self.lifecycle.active = bool(value)

    @property
    def _closed(self) -> bool:
        return self.lifecycle.closed

    @_closed.setter
    def _closed(self, value: bool) -> None:
        self.lifecycle.closed = bool(value)

    @property
    def _turn_generation(self) -> int:
        return self.lifecycle.turn_generation

    @_turn_generation.setter
    def _turn_generation(self, value: int) -> None:
        self.lifecycle.turn_generation = int(value)

    @property
    def _capture_generation(self) -> int:
        return self.lifecycle.capture_generation

    @_capture_generation.setter
    def _capture_generation(self, value: int) -> None:
        self.lifecycle.capture_generation = int(value)

    @property
    def _wake_generation(self) -> int:
        return self.lifecycle.wake_generation

    @_wake_generation.setter
    def _wake_generation(self, value: int) -> None:
        self.lifecycle.wake_generation = int(value)

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
                vad_end_threshold=self.config.asr.vad_end_threshold,
                min_silence_duration_ms=self.config.asr.min_silence_duration_ms,
                speculative_inference_enabled=self.config.asr.speculative_inference_enabled,
                speculative_start_silence_ms=self.config.asr.speculative_start_silence_ms,
                speculative_min_confidence=self.config.asr.speculative_min_confidence,
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
                on_speculative_transcript_callback=self._on_asr_speculative_transcript,
                on_speculative_invalidated_callback=self._on_asr_speculative_invalidated,
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

    async def replace_llm_engine(self, llm_engine: BaseLLM) -> None:
        """Hot-swap the LLM used by subsequent turns without dropping the session."""
        self._cancel_history_summary()
        self._cancel_speculative_llm("llm_engine_replaced")
        if self.current_cancel_event is not None:
            self.current_cancel_event.set()
        task = self.current_turn_task
        current = asyncio.current_task()
        if task is not None and not task.done() and task is not current:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.current_turn_task = None
        self.current_cancel_event = None
        self.llm_engine = llm_engine
        self.turn_runner = TurnRunner(llm_engine)

    async def reload_asr_engine(self) -> None:
        """Prepare a replacement ASR first, then swap it into the live session."""
        self._cancel_speculative_llm("asr_engine_reloaded")
        replacement = self._create_asr()
        await replacement.start()
        previous = self.asr
        self.asr = replacement
        try:
            await previous.stop()
        except Exception as exc:
            logger.debug("Previous ASR cleanup failed during hot reload: %s", exc)

    def reload_session_policy(self) -> None:
        policy = str(self.config.server.barge_in_policy or "client_only").strip().lower()
        if policy != "client_only":
            logger.warning(
                "Unsupported barge_in_policy=%r; falling back to client_only",
                policy,
            )
            policy = "client_only"
        self.barge_in_policy = policy

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
            requested_output = str(audio_params.get("output_format", "opus")).lower()
            if requested_output in ("opus", "pcm16", "linear16"):
                self.output_audio_format = requested_output
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
                frame_duration_ms=self.config.tts.frame_duration_ms,
                output_format=self.output_audio_format,
            )
            await self.send_text(hello_resp)
            if self.mcp_device is not None:
                await self.mcp_device.on_hello(features.get("mcp") is True)
            logger.info(
                "Handshake acknowledged for session %s (Ver=%s, mode=%s, reported_server_aec=%s, barge_in_policy=%s, input_frame=%sms, output=%s)",
                self.session_id,
                self.version,
                self.listening_mode,
                self.server_side_aec_requested,
                self.barge_in_policy,
                self.codec.in_frame_duration_ms,
                self.output_audio_format,
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
                was_music_playing = self.music_player.playing
                requested_mode = str(data.get("mode", "")).strip().lower()
                if requested_mode in {"realtime", "auto", "manual"}:
                    self.listening_mode = requested_mode
                if not pending_wake:
                    self._abort_turn()
                # When the user interrupts active music, pause rather than
                # destroying the player task. The next AI turn then gets a
                # one-shot structured action gate: it must explicitly choose
                # a real tool (for example music_play/music_control) or the
                # no-action marker. This prevents an ungrounded spoken claim
                # such as "mình đổi bài rồi" without any tool receipt.
                if was_music_playing:
                    await self.music_player.pause()
                    self._music_ducked = True
                    self._music_interruption_gate = True
                self._invalidate_capture()
                self._playback_guard_until = 0.0
                if was_speaking and not was_music_playing:
                    # music_player.pause() already closes the stock TTS
                    # envelope. Avoid sending a duplicate tts:stop here.
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
                logger.info("Listen detect received chars=%d", len(user_text))
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
                # Every explicit text/chat message is a new user turn, even if
                # it repeats the previous sentence verbatim. processed_transcript
                # only de-duplicates callbacks within one capture; carrying it
                # across browser chat messages can otherwise drop a repeated
                # prompt without any response.
                self.processed_transcript = ""
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
            # Explicit user interrupt also stops music; a new turn may follow.
            if self.music_player.playing:
                await self.music_player.stop(announce=False)
                self._music_ducked = False
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
        self._cancel_speculative_llm("turn_abort")
        self.playback.invalidate(owner="assistant")
        if self.music_player is not None:
            self.music_player.clear_pending()
        self.lifecycle.abort_turn(current_task=asyncio.current_task())
        self._fixed_response_kind = None

    def _invalidate_capture(self):
        self._cancel_speculative_llm("capture_invalidated")
        capture_generation = self.lifecycle.advance_capture()
        # Transcript de-duplication is scoped to one capture. The same phrase
        # spoken again after a new capture is a new user turn.
        self.processed_transcript = ""
        invalidate = getattr(self.asr, "invalidate_capture", None)
        if invalidate is not None:
            invalidate(capture_generation)

    def _owns_turn(self, turn_generation: int) -> bool:
        return self.lifecycle.owns_turn(turn_generation)

    def _claim_assistant_playback(self, turn_generation: int) -> PlaybackLease:
        return self.playback.claim("assistant", turn_generation)

    def _owns_assistant_playback(
        self,
        lease: Optional[PlaybackLease],
        turn_generation: int,
    ) -> bool:
        return self._owns_turn(turn_generation) and self.playback.is_current(lease)

    def _cancel_history_summary(self) -> None:
        task = self._history_summary_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._history_summary_task = None

    def _schedule_history_summary(self) -> None:
        cfg = self.config.conversation
        if not cfg.history_summary_enabled or not self.is_active:
            return
        if not self.dialogue.history_summary_due(
            high_water_turns=cfg.history_summary_high_water_turns,
            min_new_turns=cfg.history_summary_min_new_turns,
        ):
            return
        if self._history_summary_task is not None and not self._history_summary_task.done():
            return

        snapshot_revision, _summary_revision, turns, previous_summary = (
            self.dialogue.summary_snapshot(keep_recent_turns=2)
        )
        if not turns:
            return

        async def worker() -> None:
            try:
                defer_seconds = max(0, cfg.history_summary_defer_ms) / 1000.0
                if defer_seconds:
                    await asyncio.sleep(defer_seconds)
                if not self.is_active:
                    return
                summarizer = getattr(self.llm_engine, "summarize_history", None)
                if not callable(summarizer):
                    return
                summary = await summarizer(
                    turns,
                    previous_summary=previous_summary,
                    max_chars=cfg.history_summary_max_chars,
                )
                committed = self.dialogue.commit_history_summary(
                    summary,
                    snapshot_revision=snapshot_revision,
                )
                logger.info(
                    "history_summary session=%s committed=%s revision=%d chars=%d",
                    self.session_id,
                    committed,
                    snapshot_revision,
                    len(summary or ""),
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.debug("History summary failed session=%s: %s", self.session_id, exc)
            finally:
                if self._history_summary_task is asyncio.current_task():
                    self._history_summary_task = None

        self._history_summary_task = asyncio.create_task(worker())

    def _cancel_pending_wake(self):
        self.lifecycle.advance_wake()
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
            or (self.music_player is not None and self.music_player.playing)
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

    async def _stream_idle_farewell(self, messages, revision: int):
        """One bounded farewell generation.

        Returns the stripped text (possibly ""), or None when the flow was
        superseded by new user activity / session end.
        """
        speech: list[str] = []
        try:
            async for event in self.turn_runner.stream(
                messages,
                tools=[],
                # Goodbye prompt carries no [end]/[continue] markers; skip
                # control parsing so the farewell text passes through untouched.
                detect_end_intent=False,
                tool_choice="none",
                first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                total_timeout_ms=min(
                    self.config.latency.total_turn_timeout_ms,
                    max(100, self.config.conversation.ai_control_timeout_ms),
                ),
            ):
                if revision != self._activity_revision or not self.is_active:
                    return None
                if isinstance(event, SpeechSegmentEvent):
                    speech.append(event.text)
                elif isinstance(event, FailedEvent):
                    raise RuntimeError(event.error)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("idle goodbye generation failed session=%s error=%s", self.session_id, exc)
            return ""
        if revision != self._activity_revision or not self.is_active:
            return None
        return " ".join(part.strip() for part in speech if part.strip()).strip()

    async def _evaluate_idle_semantics(self, revision: int, timeout: float) -> bool:
        """Deterministic conversational idle deadline.

        After `idle_timeout_seconds` with no conversational interaction
        (no user question and no AI answer), the session always ends: a
        bounded LLM inference generates a farewell, it is played, then the
        transport/session is closed. There is no AI-decided [continue]
        loop — a quiet session must not linger forever. A new user turn that
        arrives mid-flow bumps the activity revision and cancels this path.

        The LLM prompt owns the semantic meaning and persona wording. Server
        validation is intentionally structural only: malformed/empty output
        or replay of the previous answer gets one retry. If AI still cannot
        produce usable output, the lifecycle closes silently rather than
        inventing a hard-coded spoken sentence.
        """
        # Idle close is a fresh lifecycle inference, not another conversation
        # turn. Never feed the previous user question/assistant answer back into
        # this call: doing so lets a model repeat the last answer at hang-up.
        previous_reply = next(
            (
                str(item.content or "")
                for item in reversed(self.dialogue.messages)
                if item.role == "assistant" and str(item.content or "").strip()
            ),
            "",
        )
        messages = [
            {
                "role": "system",
                "content": (
                    IDLE_FAREWELL_SYSTEM_PROMPT
                    + f"\nIdle deadline đã đạt sau {timeout:.0f} giây không tương tác."
                ),
            },
            {"role": "user", "content": IDLE_FAREWELL_USER_PROMPT},
        ]
        text = await self._stream_idle_farewell(messages, revision)
        if text is None:
            return False
        cleaned = (text or "").strip()
        if not _is_valid_idle_farewell(cleaned, previous_reply):
            logger.info("idle farewell retry session=%s first_chars=%d", self.session_id, len(cleaned))
            retry_messages = [
                {
                    "role": "system",
                    "content": IDLE_FAREWELL_SYSTEM_PROMPT + "\n" + IDLE_FAREWELL_RETRY_PROMPT,
                },
                {"role": "user", "content": IDLE_FAREWELL_USER_PROMPT},
            ]
            retry = await self._stream_idle_farewell(retry_messages, revision)
            if retry is None:
                return False
            cleaned = (retry or "").strip()
        if not _is_valid_idle_farewell(cleaned, previous_reply):
            logger.info(
                "idle farewell unavailable session=%s; closing without spoken fallback",
                self.session_id,
            )
            cleaned = ""

        self._closing_reason = "idle_timeout"
        if cleaned and self.config.conversation.goodbye_enabled:
            self._start_fixed_response(
                cleaned,
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
        was_music_playing = self.music_player.playing
        self._abort_turn()
        if was_music_playing:
            await self.music_player.stop(announce=False)
            self._music_ducked = False
        self._invalidate_capture()
        self._playback_guard_until = 0.0
        if was_speaking or was_music_playing:
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
        self._fixed_response_kind = kind
        task = asyncio.create_task(
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
        self.lifecycle.bind_turn(task, cancel_event)

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
        playback_lease: Optional[PlaybackLease] = None
        pacer = AudioPacer(
            frame_duration_ms=self.config.tts.frame_duration_ms,
            send_ahead_ms=self.config.tts.send_ahead_ms,
        )
        try:
            if not self._owns_turn(turn_generation):
                return
            self.state = SessionState.THINKING
            await self._duck_music_for_speech()
            playback_lease = self._claim_assistant_playback(turn_generation)
            if not self._owns_assistant_playback(playback_lease, turn_generation):
                return
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
                if cancel_event.is_set() or not self._owns_assistant_playback(playback_lease, turn_generation):
                    return
                if not await pacer.wait_for_send(cancel_event):
                    return
                if not self._owns_assistant_playback(playback_lease, turn_generation):
                    return
                if not await self.send_binary(self._pack_tts_audio(opus_frame)):
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

            if cancel_event.is_set() or not self._owns_assistant_playback(playback_lease, turn_generation):
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
            if self._owns_assistant_playback(playback_lease, turn_generation) and sent_start:
                await self.send_text(make_tts_message(self.session_id, "stop"))
            if close_after and self.is_active:
                await self._close_transport_and_session(closing_reason or kind)
            elif self._owns_turn(turn_generation):
                self.state = SessionState.IDLE if self.listening_mode == "manual" else SessionState.LISTENING
        finally:
            self.playback.release(playback_lease)
            if self.lifecycle.clear_turn_if_current():
                self._fixed_response_kind = None
                await self._unduck_music()

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
        if self.recovery_audio_cache is None:
            return False
        try:
            recovery = await self.recovery_audio_cache.get_recovery()
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
        playback_lease: Optional[PlaybackLease] = None
        try:
            await self._duck_music_for_speech()
            playback_lease = self._claim_assistant_playback(turn_generation)
            if not self._owns_assistant_playback(playback_lease, turn_generation):
                return False
            if not await self.send_text(make_tts_message(self.session_id, "start")):
                return False
            sent_start = True
            if not await self.send_text(
                make_tts_message(
                    self.session_id,
                    "sentence_start",
                    recovery.text,
                    response_kind="recovery",
                )
            ):
                return False
            self.state = SessionState.SPEAKING
            for opus_frame in frames:
                if cancel_event.is_set() or not self._owns_assistant_playback(playback_lease, turn_generation):
                    return False
                if not await pacer.wait_for_send(cancel_event):
                    return False
                if not self._owns_assistant_playback(playback_lease, turn_generation):
                    return False
                if not await self.send_binary(self._pack_tts_audio(opus_frame)):
                    return False
                sent_audio = True
                pacer.record_frame_sent()
                if pacer.playback_end is not None:
                    self._playback_guard_until = max(
                        self._playback_guard_until,
                        pacer.playback_end,
                    )
            if sent_audio and self._owns_assistant_playback(playback_lease, turn_generation) and not cancel_event.is_set():
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
            if sent_start and not sent_audio and self._owns_assistant_playback(playback_lease, turn_generation):
                await self.send_text(make_tts_message(self.session_id, "stop"))
            self.playback.release(playback_lease)

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
        playback_lease: Optional[PlaybackLease] = None
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
            await self._duck_music_for_speech()
            playback_lease = self._claim_assistant_playback(turn_generation)
            if not self._owns_assistant_playback(playback_lease, turn_generation):
                return
            if not await self.send_text(make_tts_message(self.session_id, "start")):
                raise ConnectionError("failed to send fixed-response tts:start")
            sent_start = True
            if not await self.send_text(make_tts_message(self.session_id, "sentence_start", text)):
                raise ConnectionError("failed to send fixed-response sentence_start")
            self.state = SessionState.SPEAKING

            first_binary_sent = False
            for opus_frame in frames:
                if cancel_event.is_set() or not self._owns_assistant_playback(playback_lease, turn_generation):
                    return
                if not await pacer.wait_for_send(cancel_event):
                    return
                if not self._owns_assistant_playback(playback_lease, turn_generation):
                    return
                packet = self._pack_tts_audio(opus_frame)
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

            if cancel_event.is_set() or not self._owns_assistant_playback(playback_lease, turn_generation):
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
            if self._owns_assistant_playback(playback_lease, turn_generation) and sent_start:
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
            self.playback.release(playback_lease)
            if self.lifecycle.clear_turn_if_current():
                self._fixed_response_kind = None
                await self._unduck_music()

    async def _duck_music_for_speech(self) -> None:
        """Pause music while AI speech plays so streams never overlap.

        Called at every AI-audio start (normal turns, fixed responses,
        error fallback). Idempotent within a turn via _music_ducked; the
        flag clears when the turn settles or the session closes. A manual
        music command in between wins (control paths reset the flag only
        on explicit user stop).
        """
        if self._music_ducked:
            return
        try:
            if self.music_player.state == "playing":
                await self.music_player.pause()
                self._music_ducked = True
                logger.info("Music ducked for AI speech session=%s",
                            self.session_id)
        except Exception as exc:
            logger.debug("Music duck failed: %s", exc)

    async def _unduck_music(self) -> None:
        """Resume music paused by _duck_music_for_speech, if still paused."""
        if not self._music_ducked:
            return
        self._music_ducked = False
        try:
            if self.music_player.state == "paused":
                await self.music_player.resume()
                logger.info("Music resumed after AI speech session=%s",
                            self.session_id)
        except Exception as exc:
            logger.debug("Music resume failed: %s", exc)

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

        if self.state == SessionState.SPEAKING or (self.music_player is not None and self.music_player.playing):
            # Stock FW and single-mic setups have no proof that device-side AEC
            # is effective. Under client_only policy, speech detected while TTS
            # or music is playing is treated as speaker echo. Explicit abort /
            # listen:start remains the supported interruption path.
            self._discard_asr_until_speech_final = True
            self._speech_active = False
            self.final_transcript_parts.clear()
            if self.music_player is not None and self.music_player.playing:
                logger.info("Ignoring ASR speech while music is playing (echo guard)")
            elif self.listening_mode == "realtime":
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
            logger.info("Discarding non-lexical ASR transcript chars=%d", len(transcript))
            transcript = ""

        if self._discard_asr_until_speech_final:
            logger.info(
                "Discarding ASR during TTS chars=%d final=%s speech_final=%s",
                len(transcript),
                is_final,
                speech_final,
            )
            if speech_final:
                self._discard_asr_until_speech_final = False
                self.final_transcript_parts.clear()
                self._speech_active = False
            return
        logger.info("ASR transcript received chars=%d final=%s speech_final=%s", len(transcript), is_final, speech_final)
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
                context_prefetch_task: Optional[asyncio.Task] = None
                if raw_final_text:
                    self.turn_metrics.record_capture_event(
                        self._capture_generation,
                        "context_prefetch_start",
                        chars=len(raw_final_text),
                    )
                    context_prefetch_task = asyncio.create_task(
                        self.context_builder.prefetch_memory(
                            query=raw_final_text,
                            owner_id=self._memory_owner_id,
                            session_memory=self._session_memory,
                        )
                    )
                final_text = await self._maybe_correct_asr_transcript(raw_final_text)
                if context_prefetch_task is not None and final_text != raw_final_text:
                    context_prefetch_task.cancel()
                    await asyncio.gather(context_prefetch_task, return_exceptions=True)
                    context_prefetch_task = None
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
                if not final_text and context_prefetch_task is not None:
                    context_prefetch_task.cancel()
                    await asyncio.gather(context_prefetch_task, return_exceptions=True)
                    context_prefetch_task = None
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
                        asr_confidence=getattr(self.asr, "last_word_confidence", None),
                        context_prefetch_task=context_prefetch_task,
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

    def _cancel_speculative_tts_prefetch(
        self,
        handle: Optional[_SpeculativeLLMHandle],
        reason: str = "",
    ) -> None:
        if handle is None:
            return
        cancel_event = handle.tts_prefetch_cancel_event
        task = handle.tts_prefetch_task
        was_active = task is not None and not task.done()
        had_buffered_audio = bool(
            handle.tts_prefetch_queue is not None
            and not handle.tts_prefetch_queue.empty()
        )
        if cancel_event is not None:
            cancel_event.set()
        if was_active:
            task.cancel()
        if (
            reason
            and handle.tts_prefetch_text
            and not handle.tts_prefetch_consumed
            and (was_active or had_buffered_audio)
        ):
            self.turn_metrics.record_capture_event(
                handle.capture_generation,
                "tts_speculative_discarded",
                reason=reason,
                chars=len(handle.tts_prefetch_text),
            )

    async def _run_speculative_tts_prefetch(
        self,
        handle: _SpeculativeLLMHandle,
        text: str,
    ) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        frame_ms = max(1, int(self.config.tts.frame_duration_ms))
        buffered_frames = max(
            1,
            (int(self.config.tts.send_ahead_ms) + frame_ms - 1) // frame_ms,
        )
        queue: asyncio.Queue = asyncio.Queue(maxsize=buffered_frames)
        cancel_event = asyncio.Event()
        handle.tts_prefetch_text = cleaned
        handle.tts_prefetch_queue = queue
        handle.tts_prefetch_cancel_event = cancel_event
        lead_ms = max(
            1,
            int(self.config.asr.min_silence_duration_ms)
            - int(self.config.asr.speculative_start_silence_ms),
        )
        emitted = 0
        try:
            async for opus_frame in open_tts_stream(
                self.tts_engine,
                cleaned,
                cancel_event,
                priority="live",
                queue_deadline_seconds=lead_ms / 1000.0,
            ):
                if (
                    cancel_event.is_set()
                    or handle.capture_generation != self._capture_generation
                    or not self.is_active
                ):
                    return
                await queue.put(opus_frame)
                emitted += 1
                if emitted == 1:
                    self.turn_metrics.record_capture_event(
                        handle.capture_generation,
                        "tts_speculative_first_opus_ready",
                        chars=len(cleaned),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            handle.tts_prefetch_error = exc
            self.turn_metrics.record_capture_event(
                handle.capture_generation,
                "tts_speculative_failed",
                error=type(exc).__name__,
            )
        finally:
            handle.tts_prefetch_done.set()

    def _maybe_start_speculative_tts_prefetch(
        self,
        handle: _SpeculativeLLMHandle,
        event: SpeechSegmentEvent,
    ) -> None:
        if (
            not self.config.tts.speculative_prefetch_enabled
            or handle.tts_prefetch_task is not None
            or handle.tts_prefetch_consumed
        ):
            return
        text = str(event.text or "").strip()
        if not text:
            return
        task = asyncio.create_task(
            self._run_speculative_tts_prefetch(handle, text)
        )
        handle.tts_prefetch_task = task

    async def _iter_speculative_tts_prefetch(
        self,
        handle: _SpeculativeLLMHandle,
        text: str,
        cancel_event: asyncio.Event,
    ):
        cleaned = str(text or "").strip()
        queue = handle.tts_prefetch_queue
        if (
            handle.tts_prefetch_consumed
            or queue is None
            or cleaned != handle.tts_prefetch_text
        ):
            return
        handle.tts_prefetch_consumed = True
        mark_current(
            "tts_speculative_adopted",
            chars=len(cleaned),
            buffered_frames=queue.qsize(),
        )
        while True:
            if cancel_event.is_set():
                return
            if queue.empty() and handle.tts_prefetch_done.is_set():
                if handle.tts_prefetch_error is not None:
                    raise RuntimeError("speculative TTS prefetch failed") from handle.tts_prefetch_error
                return
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            yield frame

    def _on_speculative_llm_done(
        self,
        handle: _SpeculativeLLMHandle,
        task: asyncio.Task,
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError as exc:
            handle.error = exc
        except Exception as exc:
            handle.error = exc
            logger.debug("Speculative LLM task failed: %s", exc)
        finally:
            handle.metadata_ready.set()
            handle.queue.put_nowait(_SPECULATIVE_LLM_DONE)

    def _cancel_speculative_llm(self, reason: str = "") -> None:
        handle = self._speculative_llm_handle
        self._cancel_speculative_tts_prefetch(handle, reason or "llm_cancelled")
        task = handle.task if handle is not None else self._speculative_llm_task
        generation = self._speculative_llm_capture_generation
        self._speculative_llm_task = None
        self._speculative_llm_handle = None
        self._speculative_llm_text = ""
        self._speculative_llm_capture_generation = None
        if task is not None and not task.done():
            task.cancel()
        if reason and generation is not None:
            self.turn_metrics.record_capture_event(
                generation,
                "llm_speculative_discarded",
                reason=reason,
            )

    async def _on_asr_speculative_invalidated(
        self,
        capture_generation: Optional[int] = None,
    ) -> None:
        if (
            capture_generation is not None
            and capture_generation != self._speculative_llm_capture_generation
        ):
            return
        self._cancel_speculative_llm("speech_resumed")

    @staticmethod
    def _message_fingerprint(messages: list[dict[str, Any]]) -> str:
        payload = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    async def _collect_speculative_llm_events(
        self,
        handle: _SpeculativeLLMHandle,
        user_text: str,
        *,
        asr_confidence: float,
        capture_generation: int,
    ) -> None:
        if capture_generation != self._capture_generation or not self.is_active:
            raise asyncio.CancelledError()

        persona_version = self._persona_snapshot()
        catalog_hash = self.tool_registry.catalog_fingerprint()
        base_dialogue = list(self.dialogue.get_messages_for_llm())
        base_dialogue_hash = self._message_fingerprint(base_dialogue)
        dialogue_messages = list(base_dialogue)
        dialogue_messages.append({"role": "user", "content": user_text})
        messages = await self.context_builder.build(
            dialogue_messages,
            query=user_text,
            owner_id=self._memory_owner_id,
            session_memory=self._session_memory,
        )
        if capture_generation != self._capture_generation or not self.is_active:
            raise asyncio.CancelledError()

        messages.insert(0, clock_context(self.config.server.timezone))
        music_runtime = self.music_player.status() if self.music_player is not None else None
        if music_runtime and (
            music_runtime.get("title")
            or music_runtime.get("history")
            or music_runtime.get("state") != "idle"
            or music_runtime.get("has_pending")
        ):
            messages.insert(1, {
                "role": "system",
                "content": (
                    "music_runtime="
                    + json.dumps(
                        music_runtime,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + ". Đây là trạng thái nhạc tươi do server cung cấp, không đọc thành lời. "
                      "Nếu người dùng muốn bài khác, music_play sẽ thay bài hiện tại/gần nhất và "
                      "không cần hỏi xác nhận riêng. Nếu câu trước vừa hỏi muốn nghe bài nào và "
                      "lời mới chỉ là một cụm tên bài/ca sĩ/thể loại, dùng chính cụm đó làm query "
                      "để tiếp tục phát; không hỏi lại cùng một dữ kiện."
                ),
            })

        provenance = json.dumps(
            {
                "source": "voice_asr",
                "min_word_confidence": round(float(asr_confidence), 4),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages.insert(1, {
            "role": "system",
            "content": (
                f"input_provenance={provenance}. "
                "Đây là metadata kỹ thuật, không đọc thành lời. Nếu confidence thấp hơn "
                f"{self.config.memory.min_asr_confidence_for_write:.2f} và lượt này sẽ thay đổi memory, "
                "không suy diễn fact: hỏi lại ngắn gọn để xác nhận. Các intent không liên quan memory vẫn xử lý bình thường."
            ),
        })

        business_tools = (
            self.tool_registry.openai_tools(limit=self.config.tools.schema_limit)
            if self.config.tools.enabled and self.config.tools.native_enabled
            else []
        )
        pending_action = self.pending_actions.peek()
        pending_action_id = pending_action.action_id if pending_action is not None else ""
        if pending_action is not None:
            messages.insert(
                -1 if messages and messages[-1].get("role") == "user" else len(messages),
                {
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
                },
            )

        music_action_gate = bool(self._music_interruption_gate)
        if music_action_gate:
            messages.insert(
                -1 if messages and messages[-1].get("role") == "user" else len(messages),
                {
                    "role": "system",
                    "content": (
                        "music_interruption_decision=required. Người dùng vừa ngắt một bài đang phát "
                        "để nói lượt mới. Ở round đầu này không được trả lời bằng speech: phải chọn đúng "
                        "một structured tool. Nếu họ muốn phát/đổi bài thì dùng music_play; nếu muốn "
                        "pause/resume/stop/next/previous/status thì dùng music_control; nếu là action khác "
                        "thì chọn tool action tương ứng; chỉ dùng veetee_no_action khi lời mới thật sự "
                        "không yêu cầu bất kỳ action/tool nào."
                    ),
                },
            )

        tools = business_tools + semantic_tools(
            memory_enabled=self.config.memory.enabled,
            pending_action=pending_action is not None,
            include_no_action=music_action_gate,
        )
        detect_end_intent = bool(
            self.config.intent.enabled
            and self.config.intent.semantic_end_enabled
            and self.config.conversation.end_intent_ai_enabled
        )
        messages = self._fit_llm_context(
            messages,
            tools=tools,
            detect_end_intent=detect_end_intent,
        )
        handle.metadata = {
            "text": user_text,
            "capture_generation": capture_generation,
            "pending_action_id": pending_action_id,
            "music_action_gate": music_action_gate,
            "persona_version": persona_version,
            "catalog_hash": catalog_hash,
            "base_dialogue_hash": base_dialogue_hash,
        }
        handle.metadata_ready.set()

        started = time.perf_counter()
        event_count = 0
        stream = self.turn_runner.stream(
            messages,
            tools=tools,
            detect_end_intent=detect_end_intent,
            tool_choice="required" if music_action_gate else None,
            first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
            total_timeout_ms=self.config.latency.total_turn_timeout_ms,
        )
        try:
            async for event in stream:
                if (
                    capture_generation != self._capture_generation
                    or not self.is_active
                ):
                    raise asyncio.CancelledError()
                event_count += 1
                if isinstance(event, SpeechSegmentEvent):
                    self._maybe_start_speculative_tts_prefetch(handle, event)
                await handle.queue.put(event)
        finally:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except RuntimeError:
                    pass

        self.turn_metrics.record_capture_event(
            capture_generation,
            "llm_speculative_ready",
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
            event_count=event_count,
        )

    async def _on_asr_speculative_transcript(
        self,
        transcript: str,
        confidence: float,
        capture_generation: Optional[int] = None,
    ) -> None:
        text = str(transcript or "").strip()
        generation = (
            self._capture_generation
            if capture_generation is None
            else int(capture_generation)
        )
        if (
            not text
            or not self.config.asr.speculative_llm_enabled
            or generation != self._capture_generation
            or not self.is_active
            or float(confidence) < float(self.config.asr.speculative_llm_min_confidence)
        ):
            return
        if self.current_turn_task is not None and not self.current_turn_task.done():
            return

        self._cancel_speculative_llm("superseded")
        self._speculative_llm_text = text
        self._speculative_llm_capture_generation = generation
        self.turn_metrics.record_capture_event(
            generation,
            "llm_speculative_start",
            chars=len(text),
            min_word_confidence=round(float(confidence), 4),
        )
        handle = _SpeculativeLLMHandle(
            text=text,
            capture_generation=generation,
        )
        task = asyncio.create_task(
            self._collect_speculative_llm_events(
                handle,
                text,
                asr_confidence=float(confidence),
                capture_generation=generation,
            )
        )
        handle.task = task
        self._speculative_llm_handle = handle
        self._speculative_llm_task = task
        task.add_done_callback(
            lambda completed, owned=handle: self._on_speculative_llm_done(
                owned, completed
            )
        )

    def _take_speculative_llm(
        self,
        transcript: str,
        capture_generation: int,
    ) -> Optional[_SpeculativeLLMHandle]:
        handle = self._speculative_llm_handle
        if handle is None:
            return None
        if (
            capture_generation != self._speculative_llm_capture_generation
            or str(transcript or "").strip() != self._speculative_llm_text
        ):
            self._cancel_speculative_llm("final_transcript_mismatch")
            return None
        self._speculative_llm_task = None
        self._speculative_llm_handle = None
        self._speculative_llm_text = ""
        self._speculative_llm_capture_generation = None
        return handle

    async def _trigger_ai_turn(
        self,
        transcript: str,
        *,
        check_end_intent: bool = False,
        source: str = "chat",
        asr_confidence: Optional[float] = None,
        context_prefetch_task: Optional[asyncio.Task] = None,
    ):
        if not transcript.strip():
            return

        if transcript == self.processed_transcript:
            return

        self._cancel_pending_wake()
        self._cancel_history_summary()
        self._closing_reason = None
        self._mark_conversation_activity("ai_turn")

        self.processed_transcript = transcript
        logger.info("Triggering AI turn prompt_chars=%d", len(transcript))

        speculative_llm_handle = (
            self._take_speculative_llm(
                transcript,
                self._capture_generation,
            )
            if source == "asr"
            else None
        )
        speculative_base_dialogue_hash = (
            self._message_fingerprint(self.dialogue.get_messages_for_llm())
            if speculative_llm_handle is not None
            else ""
        )
        self._abort_turn()

        turn_cancel_event = asyncio.Event()
        turn_generation = self._turn_generation
        task = asyncio.create_task(
            self._process_ai_response(
                transcript,
                turn_cancel_event,
                turn_generation,
                check_end_intent=check_end_intent,
                source=source,
                asr_confidence=asr_confidence,
                context_prefetch_task=context_prefetch_task,
                speculative_llm_handle=speculative_llm_handle,
                speculative_base_dialogue_hash=speculative_base_dialogue_hash,
            )
        )
        self.lifecycle.bind_turn(task, turn_cancel_event)

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
        asr_confidence: Optional[float] = None,
        context_prefetch_task: Optional[asyncio.Task] = None,
        speculative_llm_handle: Optional[_SpeculativeLLMHandle] = None,
        speculative_base_dialogue_hash: str = "",
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
        playback_lease: Optional[PlaybackLease] = None
        first_binary_sent = False
        reply_segments: list[str] = []
        fully_sent_segments: list[str] = []
        active_segment_text: Optional[str] = None
        active_segment_had_audio = False
        tool_calls_seen = 0
        llm_rounds = 1
        action_round_records: list[dict] = []
        # The first spoken segment is a speech commit point: pure chat starts
        # TTS immediately. Any later action event in the same round fails
        # closed and is never executed after audio may have reached the client.
        speech_committed = False
        executed_call_keys: set[tuple[str, str]] = set()
        persona_version = self._persona_snapshot()
        music_action_gate = bool(self._music_interruption_gate)
        self._music_interruption_gate = False
        # Small bounded producer/consumer queue. Scale only with configured
        # parallel read capacity instead of hiding an unrelated magic literal.
        event_queue: asyncio.Queue = asyncio.Queue(
            maxsize=max(4, self.config.tools.max_parallel_read_only * 4)
        )
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
            self.dialogue.add_user_message(user_text, turn_id=trace.turn_id)

            mark_current("context_lookup_start")
            prefetched_context: Optional[MemoryContextPrefetch] = None
            if context_prefetch_task is not None:
                try:
                    prefetched_context = await context_prefetch_task
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        return
                    mark_current(
                        "context_prefetch_ready",
                        status=prefetched_context.lookup.get("status"),
                        durable_count=prefetched_context.lookup.get("durable_count"),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    mark_current(
                        "context_prefetch_failed",
                        error=type(exc).__name__,
                    )
                    prefetched_context = None
            messages = await self.context_builder.build(
                self.dialogue.get_messages_for_llm(),
                query=user_text,
                owner_id=self._memory_owner_id,
                session_memory=self._session_memory,
                prefetched=prefetched_context,
            )
            lookup_snapshot = dict(getattr(self.context_builder, "last_lookup", {}) or {})
            mark_current(
                "context_memory",
                status=lookup_snapshot.get("status"),
                speculative_reused=bool(lookup_snapshot.get("speculative_reused")),
                returned=lookup_snapshot.get("returned"),
                durable_ids=lookup_snapshot.get("durable_ids"),
            )
            # Supply fresh facts for every turn, without classifying user text.
            # AI decides whether these facts are relevant. Never persist this
            # snapshot in dialogue or reuse a previous turn's clock.
            messages.insert(0, clock_context(self.config.server.timezone))
            if self.music_player is not None:
                music_runtime = self.music_player.status()
                if (
                    music_runtime.get("title")
                    or music_runtime.get("history")
                    or music_runtime.get("state") != "idle"
                    or music_runtime.get("has_pending")
                ):
                    messages.insert(1, {
                        "role": "system",
                        "content": (
                            "music_runtime="
                            + json.dumps(
                                music_runtime,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + ". Đây là trạng thái nhạc tươi do server cung cấp, không đọc thành lời. "
                              "Nếu người dùng muốn bài khác, music_play sẽ thay bài hiện tại/gần nhất và "
                              "không cần hỏi xác nhận riêng. Nếu câu trước vừa hỏi muốn nghe bài nào và "
                              "lời mới chỉ là một cụm tên bài/ca sĩ/thể loại, dùng chính cụm đó làm query "
                              "để tiếp tục phát; không hỏi lại cùng một dữ kiện."
                        ),
                    })
            if source == "asr":
                confidence_value = (
                    round(float(asr_confidence), 4)
                    if asr_confidence is not None else None
                )
                provenance = json.dumps(
                    {"source": "voice_asr", "min_word_confidence": confidence_value},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                messages.insert(1, {
                    "role": "system",
                    "content": (
                        f"input_provenance={provenance}. "
                        "Đây là metadata kỹ thuật, không đọc thành lời. Nếu confidence thấp hơn "
                        f"{self.config.memory.min_asr_confidence_for_write:.2f} và lượt này sẽ thay đổi memory, "
                        "không suy diễn fact: hỏi lại ngắn gọn để xác nhận. Các intent không liên quan memory vẫn xử lý bình thường."
                    ),
                })
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
            if music_action_gate:
                messages.insert(-1 if messages and messages[-1].get("role") == "user" else len(messages), {
                    "role": "system",
                    "content": (
                        "music_interruption_decision=required. Người dùng vừa ngắt một bài đang phát "
                        "để nói lượt mới. Ở round đầu này không được trả lời bằng speech: phải chọn đúng "
                        "một structured tool. Nếu họ muốn phát/đổi bài thì dùng music_play; nếu muốn "
                        "pause/resume/stop/next/previous/status thì dùng music_control; nếu là action khác "
                        "thì chọn tool action tương ứng; chỉ dùng veetee_no_action khi lời mới thật sự "
                        "không yêu cầu bất kỳ action/tool nào."
                    ),
                })
            tools = business_tools + semantic_tools(
                memory_enabled=self.config.memory.enabled,
                pending_action=pending_action is not None,
                include_no_action=music_action_gate,
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

            async def produce_events():
                nonlocal stream
                try:
                    speculative_reused = False
                    if speculative_llm_handle is not None:
                        handle = speculative_llm_handle
                        emitted_speculative_events = 0
                        try:
                            await asyncio.wait_for(
                                handle.metadata_ready.wait(),
                                timeout=max(
                                    0.001,
                                    remaining_generation_timeout_ms() / 1000.0,
                                ),
                            )
                            result = handle.metadata or {}
                            current_pending = self.pending_actions.peek()
                            current_pending_id = (
                                current_pending.action_id
                                if current_pending is not None
                                else ""
                            )
                            state_matches = (
                                result.get("text") == user_text
                                and result.get("capture_generation")
                                == self._capture_generation
                                and result.get("pending_action_id")
                                == current_pending_id
                                and bool(result.get("music_action_gate"))
                                == music_action_gate
                                and result.get("persona_version")
                                == persona_version
                                and result.get("catalog_hash")
                                == self.tool_registry.catalog_fingerprint()
                                and result.get("base_dialogue_hash")
                                == speculative_base_dialogue_hash
                            )
                            if state_matches:
                                speculative_reused = True
                                mark_current("llm_speculative_adopted")
                                while True:
                                    item = await asyncio.wait_for(
                                        handle.queue.get(),
                                        timeout=max(
                                            0.001,
                                            remaining_generation_timeout_ms() / 1000.0,
                                        ),
                                    )
                                    if item is _SPECULATIVE_LLM_DONE:
                                        if handle.error is not None:
                                            if emitted_speculative_events == 0:
                                                speculative_reused = False
                                            else:
                                                raise handle.error
                                        break
                                    if (
                                        cancel_event.is_set()
                                        or not self._owns_turn(turn_generation)
                                    ):
                                        return
                                    emitted_speculative_events += 1
                                    if emitted_speculative_events == 1:
                                        mark_current("llm_speculative_first_event_reused")
                                    await event_queue.put(item)
                                if speculative_reused:
                                    mark_current(
                                        "llm_speculative_reused",
                                        event_count=emitted_speculative_events,
                                    )
                            else:
                                self._cancel_speculative_tts_prefetch(
                                    handle, "state_changed"
                                )
                                if handle.task is not None and not handle.task.done():
                                    handle.task.cancel()
                                mark_current(
                                    "llm_speculative_discarded",
                                    reason="state_changed",
                                )
                        except asyncio.CancelledError:
                            self._cancel_speculative_tts_prefetch(
                                handle, "cancelled_before_commit"
                            )
                            if handle.task is not None and not handle.task.done():
                                handle.task.cancel()
                            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                                raise
                            mark_current(
                                "llm_speculative_discarded",
                                reason="cancelled_before_commit",
                            )
                        except Exception as exc:
                            self._cancel_speculative_tts_prefetch(
                                handle, "speculative_llm_failed"
                            )
                            if handle.task is not None and not handle.task.done():
                                handle.task.cancel()
                            if emitted_speculative_events > 0:
                                raise
                            speculative_reused = False
                            mark_current(
                                "llm_speculative_discarded",
                                reason="failed",
                                error=type(exc).__name__,
                            )

                    if not speculative_reused:
                        stream = self.turn_runner.stream(
                            messages,
                            tools=tools,
                            detect_end_intent=detect_end_intent,
                            tool_choice="required" if music_action_gate else None,
                            first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                            total_timeout_ms=remaining_generation_timeout_ms(),
                        )
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
                nonlocal tts_started, first_binary_sent, playback_lease
                nonlocal active_segment_text, active_segment_had_audio
                cleaned = str(text or "").strip()
                if not cleaned:
                    return
                if cancel_event.is_set() or not self._owns_turn(turn_generation):
                    return
                await self._duck_music_for_speech()

                if not tts_started:
                    playback_lease = self._claim_assistant_playback(turn_generation)
                    if not self._owns_assistant_playback(playback_lease, turn_generation):
                        return
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

                prefetch_usable = bool(
                    speculative_llm_handle is not None
                    and not speculative_llm_handle.tts_prefetch_consumed
                    and speculative_llm_handle.tts_prefetch_queue is not None
                    and speculative_llm_handle.tts_prefetch_text == cleaned
                )
                live_priority = "live_first" if not first_binary_sent else "live"
                mark_current(
                    "tts_enqueue",
                    chars=len(cleaned),
                    priority="speculative_reuse" if prefetch_usable else live_priority,
                )
                first_opus_for_segment = True
                segment_audio_sent = False
                active_segment_text = cleaned
                active_segment_had_audio = False

                async def deliver_opus(source):
                    nonlocal first_opus_for_segment, segment_audio_sent
                    nonlocal active_segment_had_audio, first_binary_sent
                    async for opus_frame in source:
                        if cancel_event.is_set() or not self._owns_assistant_playback(
                            playback_lease, turn_generation
                        ):
                            return
                        if first_opus_for_segment:
                            mark_current("tts_first_opus")
                            first_opus_for_segment = False
                        if not await pacer.wait_for_send(cancel_event):
                            return
                        if not self._owns_assistant_playback(
                            playback_lease, turn_generation
                        ):
                            return
                        packet = self._pack_tts_audio(opus_frame)
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

                if prefetch_usable:
                    try:
                        await deliver_opus(
                            self._iter_speculative_tts_prefetch(
                                speculative_llm_handle,
                                cleaned,
                                cancel_event,
                            )
                        )
                    except Exception:
                        if segment_audio_sent:
                            raise
                        mark_current("tts_speculative_fallback")
                        self._cancel_speculative_tts_prefetch(
                            speculative_llm_handle, "prefetch_failed_before_audio"
                        )
                        await deliver_opus(
                            open_tts_stream(
                                self.tts_engine,
                                cleaned,
                                cancel_event,
                                priority=live_priority,
                                initial_turn_audio=not first_binary_sent,
                                queue_deadline_seconds=(
                                    self.config.latency.total_turn_timeout_ms / 1000.0
                                ),
                            )
                        )
                else:
                    await deliver_opus(
                        open_tts_stream(
                            self.tts_engine,
                            cleaned,
                            cancel_event,
                            priority=live_priority,
                            initial_turn_audio=not first_binary_sent,
                            queue_deadline_seconds=(
                                self.config.latency.total_turn_timeout_ms / 1000.0
                            ),
                        )
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
                    if detect_end_intent and (
                        event.lifecycle == "end"
                        or event.intent == "end_conversation"
                    ):
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
                    if not reply_segments:
                        logger.info(
                            "Post-ASR first LLM clause ready in %.3fs chars=%d",
                            time.perf_counter() - t_start,
                            len(event.text or ""),
                        )
                        mark_current("llm_first_speech_committed")
                    speech_committed = True
                    reply_segments.append(event.text)
                    await speak_segment(event.text, event.emotion)
                    if cancel_event.is_set() or not self._owns_turn(turn_generation):
                        return
                    continue

                if isinstance(event, MemoryProposalEvent):
                    if speech_committed:
                        mark_current("action_rejected_after_speech_commit", action="memory")
                        raise RuntimeError("memory action emitted after speech commit")
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
                    if source == "asr" and (
                        asr_confidence is None
                        or float(asr_confidence) < self.config.memory.min_asr_confidence_for_write
                    ):
                        applied = MemoryApplyResult("needs_clarification", scope="personal" if self._memory_owner_id else "session")
                        mark_current(
                            "memory_proposal_rejected",
                            reason="low_asr_confidence",
                            confidence=asr_confidence,
                            minimum=self.config.memory.min_asr_confidence_for_write,
                        )
                        receipt = make_receipt(
                            call_id=event.call_id, name=MEMORY_TOOL_NAME,
                            arguments=memory_args, status=applied.status,
                            turn_id=trace.turn_id, changed=False,
                            error="voice transcript confidence is too low for a memory mutation; ask the user to confirm",
                            provenance="memory:asr_confidence_gate",
                        )
                        action_round_records.append({
                            "call_id": event.call_id,
                            "name": MEMORY_TOOL_NAME,
                            "arguments": memory_args,
                            "receipt": receipt,
                        })
                        continue
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
                    if speech_committed:
                        mark_current("action_rejected_after_speech_commit", action="confirmation")
                        raise RuntimeError("confirmation action emitted after speech commit")
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
                    if speech_committed:
                        mark_current("action_rejected_after_speech_commit", action=event.name)
                        raise RuntimeError("tool action emitted after speech commit")
                    if event.name == NO_ACTION_TOOL_NAME:
                        tool_calls_seen += 1
                        mark_current("semantic_no_action_selected", gate="music_interruption")
                        action_round_records.append({
                            "call_id": event.call_id,
                            "name": NO_ACTION_TOOL_NAME,
                            "arguments": {},
                            "receipt": make_receipt(
                                call_id=event.call_id,
                                name=NO_ACTION_TOOL_NAME,
                                arguments={},
                                status="no_action",
                                turn_id=trace.turn_id,
                                data={"reason": "structured semantic no-action"},
                                provenance="semantic:no_action",
                            ),
                        })
                        continue
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
                    if result.status == ToolStatus.SUCCEEDED:
                        data = result.data if isinstance(result.data, dict) else {}
                        music_action_applied = (
                            event.name == "music_play" and data.get("status") == "ready"
                        ) or (
                            event.name == "music_control"
                            and str(event.arguments.get("action") or "") != "status"
                        )
                        if music_action_applied:
                            # A real music mutation supersedes the temporary
                            # pause created by the interruption gate. Let the
                            # normal speech ducking logic own any later resume.
                            self._music_ducked = False
                    action_round_records.append({
                        "call_id": event.call_id,
                        "name": event.name,
                        "arguments": event.arguments,
                        "receipt": receipt,
                    })
                    continue

                if isinstance(event, FailedEvent):
                    if event.code == "capacity_exhausted":
                        raise _ExpectedTurnFailure(
                            event.error,
                            code=event.code,
                        )
                    raise RuntimeError(event.error)

                if isinstance(event, CompletedEvent):
                    usage_fields = normalize_llm_usage(event.usage)
                    if usage_fields:
                        mark_current("llm_usage", round=1, **usage_fields)
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

            if action_round_records and speech_committed:
                # Defensive invariant; normal providers reject this before an
                # action event reaches the session.
                raise RuntimeError("action round reached terminal after speech commit")
            if speech_committed:
                mark_current("speech_streamed_before_terminal", segments=len(reply_segments))

            # Receipts are data, never user-facing prose. Every tool/memory
            # result goes back to the LLM so intent, persona, language and
            # amount of detail remain AI-authored. Deterministic server code
            # validates/applies actions but never renders their spoken result.
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
                        # Synthesis rounds contain no new user utterance.
                        # Semantic side effects (memory/confirmation) are only
                        # decided from fresh user input in the first round;
                        # receipt synthesis may chain business tools only.
                        followup_semantic_tools = []
                        fitted_round = self._fit_llm_context(
                            round_messages,
                            tools=(business_tools + followup_semantic_tools)
                            if allow_follow_tools else [],
                            detect_end_intent=bool(detect_end_intent),
                        )
                        mark_current("llm_round_start", round=synthesized_round,
                                     purpose="action_receipt_synthesis",
                                     allow_tools=allow_follow_tools)
                        round_speech: list[tuple[str, Optional[str]]] = []
                        round_tools: list[ToolCallReadyEvent] = []
                        round_memory: list[MemoryProposalEvent] = []
                        round_completed = False
                        round_failed = False
                        async for event in self.turn_runner.stream(
                            fitted_round,
                            tools=(business_tools + followup_semantic_tools)
                            if allow_follow_tools else [],
                            detect_end_intent=bool(detect_end_intent),
                            tool_choice=None if allow_follow_tools else "none",
                            first_event_timeout_ms=self.config.latency.first_token_timeout_ms,
                            total_timeout_ms=remaining_generation_timeout_ms(),
                        ):
                            if cancel_event.is_set() or not self._owns_turn(turn_generation):
                                return
                            if isinstance(event, ControlEvent):
                                current_emotion = event.emotion or current_emotion
                                if detect_end_intent and (
                                    event.lifecycle == "end"
                                    or event.intent == "end_conversation"
                                ):
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
                                # Defensive guard for provider/test doubles
                                # that emit a semantic event for a tool that
                                # was not exposed in this synthesis round.
                                mark_current(
                                    "confirmation_rejected",
                                    reason="no_new_user_input",
                                )
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
                                mark_current(
                                    "llm_round_failed",
                                    round=synthesized_round,
                                    error=event.error,
                                )
                                logger.warning(
                                    "LLM synthesis round failed round=%s error=%s",
                                    synthesized_round,
                                    event.error,
                                )
                                round_failed = True
                                break
                            if isinstance(event, CompletedEvent):
                                usage_fields = normalize_llm_usage(event.usage)
                                if usage_fields:
                                    mark_current(
                                        "llm_usage",
                                        round=synthesized_round,
                                        **usage_fields,
                                    )
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
                        if round_tools or round_memory:
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
                            # Memory follow-ups are applied in order.
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
                        # Preserve truthful receipts and never invent the tool
                        # outcome. Recovery wording is generated by the active
                        # AI persona at startup and cached; server logic only
                        # decides that a recovery is needed.
                        self.dialogue.add_system_message(_degraded_note(), turn_id=trace.turn_id)
                        recovery_text = ""
                        if self.recovery_audio_cache is not None:
                            try:
                                recovery = await self.recovery_audio_cache.get_recovery()
                                recovery_text = str(recovery.text or "").strip()
                            except Exception as exc:
                                logger.info("action receipt recovery unavailable: %s", exc)
                        if recovery_text:
                            reply_segments.append(recovery_text)
                            await speak_segment(recovery_text, "neutral")
                            mark_current("action_receipt_degraded_recovery_spoken",
                                         record_count=len(action_round_records))
                        else:
                            mark_current("action_receipt_degraded_no_speech",
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

            music_handoff = bool(
                self.music_player is not None
                and self.music_player.has_pending
                and tts_started
                and not close_reason
            )
            if tts_started and not music_handoff:
                if not self._owns_assistant_playback(playback_lease, turn_generation):
                    return
                if not await self.send_text(make_tts_message(self.session_id, "stop")):
                    raise ConnectionError("failed to send tts:stop")
                logger.info(
                    "Post-ASR tts:stop sent in %.3fs",
                    time.perf_counter() - t_start,
                )
            elif music_handoff:
                logger.info(
                    "Music handoff keeps TTS envelope open session=%s",
                    self.session_id,
                )

            complete_text = " ".join(reply_segments).strip()
            structured_receipts = [dict(record.get("receipt") or {}) for record in action_round_records]
            if complete_text:
                self.dialogue.add_assistant_message(
                    complete_text, playback="sent" if first_binary_sent else "generated",
                    turn_id=trace.turn_id,
                )
            if structured_receipts:
                # Read-only receipts (clock/calculator/search) are already
                # reflected in the assistant's spoken answer and must not be
                # promoted into a high-priority system message on the next turn.
                # Persist only stateful/side-effect receipts for follow-up
                # reconciliation.
                history_receipts = []
                for record in action_round_records:
                    record_name = str(record.get("name") or "")
                    if record_name == NO_ACTION_TOOL_NAME:
                        continue
                    descriptor = self.tool_registry.get(record_name)
                    if descriptor is not None and descriptor.read_only:
                        continue
                    receipt = record.get("receipt")
                    if isinstance(receipt, dict):
                        history_receipts.append(dict(receipt))
                if history_receipts:
                    self.dialogue.add_tool_receipts(
                        history_receipts,
                        turn_id=trace.turn_id,
                    )
            self.dialogue.record_structured_turn(
                turn_id=trace.turn_id, user_text=user_text,
                assistant_text=complete_text, receipts=structured_receipts,
                playback="sent" if first_binary_sent else ("generated" if complete_text else "unknown"),
            )
            self._schedule_history_summary()

            logger.info(
                "Completed post-ASR response pipeline in %.3fs response_chars=%d",
                time.perf_counter() - t_start,
                len(complete_text),
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
            if self.music_player is not None:
                await self.music_player.start_pending(reuse_envelope=music_handoff)

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
            if isinstance(exc, _ExpectedTurnFailure):
                logger.warning(
                    "AI turn failed with expected bounded condition code=%s: %s",
                    exc.code,
                    exc,
                )
                if trace.outcome == "running":
                    trace.finish("failed", error=exc.code)
            else:
                logger.error(
                    "Error during AI response processing: %s",
                    exc,
                    exc_info=True,
                )
                if trace.outcome == "running":
                    trace.finish("failed", error=type(exc).__name__)
            if (
                not first_binary_sent
                and tool_calls_seen == 0
                and not action_round_records
            ):
                self.dialogue.discard_unanswered_turn(trace.turn_id)
            if self._owns_turn(turn_generation):
                if tts_started and self._owns_assistant_playback(playback_lease, turn_generation):
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
            self._cancel_speculative_tts_prefetch(
                speculative_llm_handle, "turn_finished"
            )
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
            self.playback.release(playback_lease)
            if self.lifecycle.clear_turn_if_current():
                await self._unduck_music()

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

    def _pack_tts_audio(self, opus_frame: bytes) -> bytes:
        payload = opus_frame
        if self.output_audio_format in ("pcm16", "linear16"):
            payload = self.codec.decode_output_opus_to_pcm16(opus_frame)
            if not payload:
                raise RuntimeError("failed to decode outgoing TTS Opus to PCM16")
        return pack_audio_payload(payload, self.version)

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
        if not self.lifecycle.begin_close():
            return
        self._conversation_armed = False
        self._closing_reason = None

        if self.mcp_device is not None:
            await self.mcp_device.close()

        self._music_ducked = False
        try:
            await self.music_player.close()
        except Exception as exc:
            logger.debug("Music player close failed: %s", exc)
        finally:
            # No producer may retain playback authority after session teardown.
            self.playback.invalidate()

        current = asyncio.current_task()
        tasks = []
        for task in (
            self.current_turn_task,
            self._speculative_llm_task,
            self._pending_wake_task,
            self._idle_watchdog_task,
            self._history_summary_task,
        ):
            if task is not None and not task.done() and task is not current:
                task.cancel()
                tasks.append(task)
        self.current_turn_task = None
        self.current_cancel_event = None
        self._pending_wake_task = None
        self._idle_watchdog_task = None
        self._history_summary_task = None
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._invalidate_capture()
        try:
            await self.asr.stop()
        finally:
            if close_transport:
                await self._close_websocket(code=1000, reason="session_closed")
        logger.info(f"Session {self.session_id} closed")
