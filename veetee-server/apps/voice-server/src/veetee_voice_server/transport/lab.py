from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from time import monotonic
from typing import Any

import numpy as np
import soxr  # type: ignore[import-untyped]
from fastapi import WebSocket, WebSocketDisconnect
from jsonschema import Draft202012Validator

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.arbiter import ConversationState, TurnArbiter
from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    iterate_operation,
)
from veetee_voice_server.conversation.engine import ConversationEngine
from veetee_voice_server.conversation.inactivity import InactivityController
from veetee_voice_server.conversation.types import (
    AdmissionDisposition,
    ConversationOutput,
    OutputKind,
    Transcript,
    WakeSource,
)
from veetee_voice_server.manager import LabSessionContext, SessionProfile
from veetee_voice_server.providers.contracts import ToolBroker, TtsProvider
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.silero_vad import SileroVadModel, SileroVadSession
from veetee_voice_server.transport.protocol import MAX_CONTROL_FRAME_BYTES, ProtocolViolationError
from veetee_voice_server.transport.session_registry import DeviceSessionRegistry

MAX_LAB_PCM_FRAME_BYTES = 128 * 1024
MAX_LAB_TEXT_CHARACTERS = 4_000

EngineFactory = Callable[
    [TurnArbiter, "LabConversationSink", SessionProfile, ToolBroker], ConversationEngine
]


class LabConversationSink:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        session_id: str,
        output_sample_rate: int,
    ) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._output_sample_rate = output_sample_rate
        self._started_at = monotonic()
        self._wire_lock = asyncio.Lock()
        self._cancel_generation = 0
        self._tts_generation: int | None = None
        self._first_audio_generation: int | None = None

    async def emit(self, output: ConversationOutput) -> None:
        if output.generation < self._cancel_generation:
            return
        if output.kind is OutputKind.ADMISSION:
            await self.send_event(
                "admission.final",
                output.payload,
                turn_id=output.turn_id,
                generation=output.generation,
            )
            return
        if output.kind is OutputKind.PLAN:
            await self.send_event(
                "planner.final",
                output.payload,
                turn_id=output.turn_id,
                generation=output.generation,
            )
            return
        if output.kind is OutputKind.TEXT_DELTA:
            await self.send_event(
                "llm.delta",
                output.payload,
                turn_id=output.turn_id,
                generation=output.generation,
            )
            return
        if output.kind is OutputKind.TTS_START:
            self._tts_generation = output.generation
            self._first_audio_generation = None
            await self.send_event(
                "tts.start", {}, turn_id=output.turn_id, generation=output.generation
            )
            return
        if output.kind is OutputKind.AUDIO and output.audio is not None:
            await self._send_audio(output)
            return
        if output.kind is OutputKind.TTS_STOP:
            if self._tts_generation != output.generation:
                return
            await self.send_event(
                "tts.stop", {}, turn_id=output.turn_id, generation=output.generation
            )
            self._tts_generation = None
            self._first_audio_generation = None
            return
        if output.kind is OutputKind.ERROR:
            await self.send_event(
                "turn.error",
                output.payload,
                turn_id=output.turn_id,
                generation=output.generation,
            )

    async def send_event(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        turn_id: str | None = None,
        generation: int | None = None,
    ) -> None:
        event = {
            "type": "lab.event",
            "session_id": self._session_id,
            "event": name,
            "elapsed_ms": round((monotonic() - self._started_at) * 1_000, 3),
            "generation": max(self._cancel_generation, generation or 0),
            "payload": payload or {},
            **({"turn_id": turn_id} if turn_id else {}),
        }
        await self._send_json(event)

    async def send_hello(
        self, context: LabSessionContext, profile: SessionProfile, tools: ToolBroker
    ) -> None:
        await self._send_json(
            {
                "type": "lab.hello",
                "version": 1,
                "session_id": self._session_id,
                "agent": {
                    "id": context.agent_id,
                    "version": context.config_version,
                    "locale": profile.locale,
                },
                "prompt": {
                    "applied": True,
                    "version": context.config_version,
                    "language": profile.prompt.language,
                    "personality": profile.prompt.personality_preset_id,
                },
                "input_mode": context.input_mode,
                "mcp_mode": context.mcp_mode,
                "audio": {
                    "input_encoding": "pcm_s16le",
                    "input_sample_rate": 16_000,
                    "output_encoding": "pcm_s16le",
                    "output_sample_rate": self._output_sample_rate,
                    "channels": 1,
                },
                "fidelity": {
                    "vad_asr": "real" if context.input_mode != "text" else "bypassed",
                    "admission_llm_tts": "real",
                    "device_opus_transport": "not_measured",
                    "physical_aec_speaker": "not_measured",
                },
                "tools": tools.list_tools(),
            }
        )

    async def cancel_tts(self, generation: int) -> None:
        self._cancel_generation = max(self._cancel_generation, generation)
        if self._tts_generation is not None:
            await self.send_event(
                "tts.stop",
                {"cancelled": True},
                generation=generation,
            )
        self._tts_generation = None
        self._first_audio_generation = None

    def mark_cancelled(self, generation: int) -> None:
        self._cancel_generation = max(self._cancel_generation, generation)

    async def _send_audio(self, output: ConversationOutput) -> None:
        audio = output.audio
        if (
            audio is None
            or audio.encoding != "pcm_s16le"
            or self._tts_generation != output.generation
        ):
            return
        pcm = audio.data
        if audio.sample_rate != self._output_sample_rate:
            pcm = await asyncio.to_thread(
                self._resample_pcm,
                pcm,
                audio.sample_rate,
                self._output_sample_rate,
            )
        if self._first_audio_generation != output.generation:
            self._first_audio_generation = output.generation
            await self.send_event(
                "tts.first_audio",
                {"sample_rate": self._output_sample_rate},
                turn_id=output.turn_id,
                generation=output.generation,
            )
        async with self._wire_lock:
            await self._websocket.send_bytes(pcm)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_CONTROL_FRAME_BYTES:
            raise ProtocolViolationError("Lab control frame exceeds 8 KiB", close_code=1009)
        async with self._wire_lock:
            await self._websocket.send_text(encoded)

    @staticmethod
    def _resample_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        converted = soxr.resample(samples, source_rate, target_rate, quality="HQ")
        return bytes((np.clip(converted, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())


class EmptyLabToolBroker:
    def list_tools(self) -> list[dict[str, Any]]:
        return []

    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any:
        raise KeyError(f"Lab MCP is disabled: {name}")


class SimulatedLabToolBroker:
    def __init__(self) -> None:
        self._volume = 55
        self._brightness = 80
        self._tools: dict[str, dict[str, Any]] = {
            "self.get_device_status": {
                "name": "self.get_device_status",
                "description": "Read simulated battery, network, speaker and display state.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                "audience": "regular",
                "safetyClass": "read_only",
                "requiresConfirmation": False,
            },
            "self.audio_speaker.set_volume": {
                "name": "self.audio_speaker.set_volume",
                "description": "Set simulated speaker volume from 0 to 100.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["volume"],
                    "properties": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}},
                },
                "audience": "regular",
                "safetyClass": "reversible",
                "requiresConfirmation": False,
            },
            "self.screen.set_brightness": {
                "name": "self.screen.set_brightness",
                "description": "Set simulated display brightness from 5 to 100.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["brightness"],
                    "properties": {
                        "brightness": {"type": "integer", "minimum": 5, "maximum": 100}
                    },
                },
                "audience": "regular",
                "safetyClass": "reversible",
                "requiresConfirmation": False,
            },
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools.values())

    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any:
        context.checkpoint()
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown simulated MCP tool: {name}")
        validation_error = next(
            Draft202012Validator(tool["inputSchema"]).iter_errors(arguments), None
        )
        if validation_error is not None:
            raise ValueError(f"Invalid simulated MCP arguments: {validation_error.message}")
        if name == "self.audio_speaker.set_volume":
            self._volume = int(arguments["volume"])
        elif name == "self.screen.set_brightness":
            self._brightness = int(arguments["brightness"])
        return {
            "simulated": True,
            "batteryPercent": 78,
            "network": "lab-loopback",
            "volume": self._volume,
            "brightness": self._brightness,
        }


class SelectedDeviceLabToolBroker:
    def __init__(
        self,
        registry: DeviceSessionRegistry,
        device_id: str,
        catalog: list[dict[str, Any]],
    ) -> None:
        self._registry = registry
        self._device_id = device_id
        self._catalog = catalog

    def list_tools(self) -> list[dict[str, Any]]:
        return self._catalog

    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any:
        return await self._registry.call_ai(self._device_id, name, arguments, context)


class InstrumentedLabToolBroker:
    def __init__(self, broker: ToolBroker, sink: LabConversationSink) -> None:
        self._broker = broker
        self._sink = sink

    def list_tools(self) -> list[dict[str, Any]]:
        return self._broker.list_tools()

    async def call(
        self, name: str, arguments: dict[str, Any], context: OperationContext
    ) -> Any:
        started_at = monotonic()
        await self._sink.send_event(
            "mcp.start",
            {"tool": name},
            turn_id=context.turn_id,
            generation=context.generation,
        )
        try:
            result = await self._broker.call(name, arguments, context)
        except BaseException as error:
            if not context.token.cancelled:
                await self._sink.send_event(
                    "mcp.error",
                    {
                        "tool": name,
                        "duration_ms": round((monotonic() - started_at) * 1_000, 3),
                        "error_type": type(error).__name__,
                    },
                    turn_id=context.turn_id,
                    generation=context.generation,
                )
            raise
        context.checkpoint()
        await self._sink.send_event(
            "mcp.stop",
            {
                "tool": name,
                "duration_ms": round((monotonic() - started_at) * 1_000, 3),
            },
            turn_id=context.turn_id,
            generation=context.generation,
        )
        return result


class LabSession:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        settings: Settings,
        context: LabSessionContext,
        profile: SessionProfile,
        asr: SherpaZipformerAsrProvider,
        vad_model: SileroVadModel,
        tts: TtsProvider,
        tool_broker: ToolBroker,
        engine_factory: EngineFactory,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.context = context
        self.profile = profile
        self.session_id = context.session_id
        self.arbiter = TurnArbiter(self.session_id)
        self.sink = LabConversationSink(
            websocket,
            session_id=self.session_id,
            output_sample_rate=settings.wire_sample_rate,
        )
        self.tools = InstrumentedLabToolBroker(tool_broker, self.sink)
        self.engine = engine_factory(self.arbiter, self.sink, profile, self.tools)
        self.asr = asr
        self.tts = tts
        self.vad = SileroVadSession(
            vad_model,
            threshold=settings.vad_threshold,
            release_threshold=settings.vad_release_threshold,
            min_silence_ms=settings.vad_min_silence_ms,
            max_speech_seconds=settings.max_utterance_seconds,
        )
        self.inactivity = InactivityController(
            arbiter=self.arbiter,
            first_input_seconds=profile.policy.first_input_seconds,
            between_turns_seconds=profile.policy.between_turns_seconds,
            closing_grace_seconds=profile.policy.closing_grace_seconds,
            max_session_seconds=profile.policy.max_session_seconds,
            goodbye=self._goodbye,
        )
        self._speech = bytearray()
        self._pre_roll = bytearray()
        self._pre_roll_bytes = settings.input_sample_rate * settings.vad_pre_roll_ms // 1_000 * 2
        self._speech_active = False
        self._audio_open = False
        self._processing_task: asyncio.Task[None] | None = None
        self._asr_context: OperationContext | None = None
        self._closed = False

    async def run(self) -> None:
        try:
            await self.sink.send_hello(self.context, self.profile, self.tools)
            await self.inactivity.assistant_opened(WakeSource.BUTTON)
            await self.sink.send_event("session.opened", {"source": "web_lab"}, generation=1)
            await self.sink.send_event("listen.start", {"source": "button"}, generation=1)
            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_pcm(message["bytes"])
                elif message.get("text") is not None:
                    if await self._handle_control(message["text"]):
                        break
        except WebSocketDisconnect:
            pass
        except ProtocolViolationError as error:
            await self.websocket.close(code=error.close_code, reason=error.reason)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._cancel_processing("lab_socket_closed")
        await self.inactivity.close()
        await self.arbiter.abort("lab_socket_closed")

    async def _handle_control(self, raw: str) -> bool:
        if len(raw.encode("utf-8")) > MAX_CONTROL_FRAME_BYTES:
            raise ProtocolViolationError("Lab control frame too large", close_code=1009)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ProtocolViolationError("Malformed Lab JSON") from error
        if not isinstance(payload, dict) or payload.get("session_id") != self.session_id:
            raise ProtocolViolationError("Lab session id mismatch", close_code=1008)
        event_type = payload.get("type")
        if event_type == "lab.text":
            if self.context.input_mode != "text":
                raise ProtocolViolationError("Text input is disabled for this Lab session")
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_LAB_TEXT_CHARACTERS:
                raise ProtocolViolationError("Invalid Lab text input")
            await self._submit_text(text.strip())
            return False
        if event_type == "lab.audio.start":
            if self.context.input_mode not in {"audio_replay", "live_mic"}:
                raise ProtocolViolationError("Audio input is disabled for this Lab session")
            if payload.get("encoding") != "pcm_s16le" or payload.get("sample_rate") != 16_000:
                raise ProtocolViolationError("Lab audio must be mono PCM16 at 16 kHz")
            self._audio_open = True
            self._reset_input()
            await self.sink.send_event("audio.capture.start", {"mode": self.context.input_mode})
            return False
        if event_type == "lab.audio.end":
            if not self._audio_open:
                raise ProtocolViolationError("Lab audio capture is not open")
            await self._finish_audio()
            self._audio_open = False
            return False
        if event_type == "lab.abort":
            reason = payload.get("reason") or "web_interrupt"
            if not isinstance(reason, str) or not reason or len(reason) > 64:
                raise ProtocolViolationError("Invalid Lab abort reason")
            await self._abort_current(reason)
            return False
        if event_type == "lab.wake":
            if self.arbiter.snapshot.state is not ConversationState.STANDBY:
                await self.sink.send_event(
                    "input.busy", {"state": self.arbiter.snapshot.state.value}
                )
                return False
            await self.inactivity.assistant_opened(WakeSource.BUTTON)
            await self.sink.send_event("listen.start", {"source": "button"})
            return False
        if event_type == "lab.close":
            await self.inactivity.assistant_closed("web_lab_closed")
            await self.sink.send_event("session.closed", {"reason": "user"})
            return True
        raise ProtocolViolationError("Unsupported Lab control event")

    async def _submit_text(self, text: str) -> None:
        if self.arbiter.snapshot.state is not ConversationState.LISTENING:
            await self.sink.send_event("input.busy", {"state": self.arbiter.snapshot.state.value})
            return
        if self._processing_task is not None and not self._processing_task.done():
            await self.sink.send_event("input.busy", {"state": "processing"})
            return
        await self.inactivity.candidate_started()
        await self.sink.send_event("vad.bypassed", {"reason": "typed_text"})
        await self.sink.send_event("asr.bypassed", {"reason": "typed_text"})
        transcript = Transcript(text, self.profile.locale, confidence=1.0, stability=1.0)
        await self.sink.send_event(
            "stt.final",
            {
                "text": text,
                "locale": transcript.locale,
                "confidence": transcript.confidence,
                "source": "typed_text",
            },
        )
        self._processing_task = asyncio.create_task(self._run_transcript(transcript))

    async def _handle_pcm(self, pcm: bytes) -> None:
        if self.context.input_mode not in {"audio_replay", "live_mic"} or not self._audio_open:
            raise ProtocolViolationError("Unexpected Lab PCM frame")
        if not pcm or len(pcm) > MAX_LAB_PCM_FRAME_BYTES or len(pcm) % 2:
            raise ProtocolViolationError("Invalid Lab PCM frame", close_code=1009)
        if self.arbiter.snapshot.state is not ConversationState.LISTENING:
            return
        if self._processing_task is not None and not self._processing_task.done():
            return
        results = self.vad.process(pcm)
        started = any(item.speech_started for item in results)
        ended = any(item.speech_ended for item in results)
        if self._speech_active:
            self._speech.extend(pcm)
        else:
            self._pre_roll.extend(pcm)
            if len(self._pre_roll) > self._pre_roll_bytes:
                del self._pre_roll[: len(self._pre_roll) - self._pre_roll_bytes]
            if started:
                await self.inactivity.candidate_started()
                self._speech.extend(self._pre_roll or pcm)
                self._pre_roll.clear()
                self._speech_active = True
                await self.sink.send_event("vad.speech_start")
        if ended and self._speech_active:
            await self.sink.send_event("vad.speech_end")
            self._start_asr()

    async def _finish_audio(self) -> None:
        if self._processing_task is not None and not self._processing_task.done():
            await self.sink.send_event("audio.capture.stop", {"processing": True})
            return
        if self._speech_active:
            silence = b"\0\0" * (self.settings.input_sample_rate // 2)
            await self._handle_pcm(silence)
        if self._speech_active and (self._processing_task is None or self._processing_task.done()):
            await self.sink.send_event("vad.speech_end", {"forced": True})
            self._start_asr()
        elif not self._speech_active and (
            self._processing_task is None or self._processing_task.done()
        ):
            self._reset_input()
            await self.inactivity.candidate_rejected()
            await self.sink.send_event("input.rejected", {"reason": "no_speech"})
        await self.sink.send_event("audio.capture.stop")

    def _start_asr(self) -> None:
        if self._processing_task is not None and not self._processing_task.done():
            return
        audio = bytes(self._speech)
        self._reset_input()
        if not audio:
            return
        self._processing_task = asyncio.create_task(self._transcribe(audio))

    async def _transcribe(self, pcm: bytes) -> None:
        context = OperationContext(
            self.session_id,
            f"asr:{self.session_id}",
            self.arbiter.snapshot.generation,
            CancellationToken(),
            monotonic() + self.settings.asr_seconds,
        )
        self._asr_context = context
        await self.sink.send_event("asr.start", {"audio_bytes": len(pcm)})
        try:
            transcript = await self.asr.transcribe_pcm(
                pcm,
                sample_rate=self.settings.input_sample_rate,
                locale=self.profile.locale,
                context=context,
            )
            if not transcript.text:
                await self.inactivity.candidate_rejected()
                await self.sink.send_event("input.rejected", {"reason": "empty_transcript"})
                return
            normalized = Transcript(
                transcript.text,
                transcript.locale,
                transcript.confidence,
                transcript.stability,
            )
            await self.sink.send_event(
                "stt.final",
                {
                    "text": normalized.text,
                    "locale": normalized.locale,
                    "confidence": normalized.confidence,
                    "stability": normalized.stability,
                    "source": self.context.input_mode,
                },
            )
            await self._run_transcript(normalized)
        finally:
            if self._asr_context is context:
                self._asr_context = None
            if self._processing_task is asyncio.current_task():
                self._processing_task = None

    async def _run_transcript(self, transcript: Transcript) -> None:
        try:
            disposition = await self.engine.handle_transcript(transcript)
            if disposition in {
                AdmissionDisposition.ACCEPTED,
                AdmissionDisposition.INTERRUPT,
                AdmissionDisposition.END,
            }:
                await self.inactivity.valid_user_activity()
                await self.inactivity.turn_completed()
            elif disposition in {
                AdmissionDisposition.NON_ACTIONABLE,
                AdmissionDisposition.NOT_ADDRESSED,
                AdmissionDisposition.UNCLEAR,
            }:
                await self.inactivity.candidate_rejected()
            elif disposition is None:
                await self.inactivity.candidate_rejected()
            state = self.arbiter.snapshot.state
            if state is ConversationState.LISTENING and disposition not in {
                AdmissionDisposition.NON_ACTIONABLE,
                AdmissionDisposition.NOT_ADDRESSED,
                AdmissionDisposition.UNCLEAR,
                AdmissionDisposition.INTERRUPT,
            }:
                await self.sink.send_event("listen.start", {"source": "turn_continuation"})
            elif state is ConversationState.STANDBY:
                await self.sink.send_event("assistant.sleep", {"reason": "semantic_end"})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.inactivity.candidate_rejected()
            await self.sink.send_event(
                "turn.error",
                {"code": "transcription_or_turn_failed", "error_type": type(error).__name__},
            )
        finally:
            if self._processing_task is asyncio.current_task():
                self._processing_task = None

    async def _abort_current(self, reason: str) -> None:
        was_closing = self.arbiter.snapshot.state is ConversationState.CLOSING
        started_at = monotonic()
        receipt = await self.arbiter.abort(reason)
        self.sink.mark_cancelled(receipt.generation)
        await self._cancel_processing(reason)
        self._reset_input()
        await self.sink.cancel_tts(receipt.generation)
        if was_closing:
            await self.inactivity.interrupt_closing()
        await self.arbiter.finish_cancellation(receipt)
        await self.sink.send_event(
            "abort.complete",
            {
                "reason": reason,
                "cancelled_turn_id": receipt.cancelled_turn_id,
                "duration_ms": round((monotonic() - started_at) * 1_000, 3),
            },
            generation=receipt.generation,
        )
        if self.arbiter.snapshot.state is ConversationState.LISTENING:
            await self.sink.send_event("listen.start", {"source": "interrupt"})
            await self.inactivity.turn_completed()

    async def _goodbye(self, reason: str) -> None:
        context = OperationContext(
            self.session_id,
            f"goodbye:{self.session_id}",
            self.arbiter.snapshot.generation,
            CancellationToken(),
            monotonic() + self.profile.policy.tts_seconds,
        )
        started = False
        try:
            async for audio in iterate_operation(
                self.tts.synthesize(self.profile.goodbye_text, self.profile.locale, context),
                context,
            ):
                if not started:
                    await self.sink.emit(
                        ConversationOutput(OutputKind.TTS_START, None, context.generation)
                    )
                    started = True
                await self.sink.emit(
                    ConversationOutput(
                        OutputKind.AUDIO,
                        None,
                        context.generation,
                        payload={"system": "goodbye", "reason": reason},
                        audio=audio,
                    )
                )
        finally:
            if started:
                await self.sink.emit(
                    ConversationOutput(OutputKind.TTS_STOP, None, context.generation)
                )
        await self.sink.send_event("assistant.sleep", {"reason": reason})

    async def _cancel_processing(self, reason: str) -> None:
        context = self._asr_context
        self._asr_context = None
        if context is not None:
            context.token.cancel(reason)
        task = self._processing_task
        self._processing_task = None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _reset_input(self) -> None:
        self._speech.clear()
        self._pre_roll.clear()
        self._speech_active = False
        self.vad.reset()
