from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

import soxr  # type: ignore[import-untyped]
import structlog
from fastapi import WebSocket, WebSocketDisconnect

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
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.providers.contracts import ToolBroker, TtsProvider
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.silero_vad import SileroVadModel, SileroVadSession
from veetee_voice_server.telemetry import ConversationTelemetry, NullConversationTelemetry
from veetee_voice_server.transport.mcp import DeviceMcpClient, DeviceMcpError
from veetee_voice_server.transport.opus import OpusDecoder, OpusEncoder, OpusError
from veetee_voice_server.transport.protocol import (
    MAX_CONTROL_FRAME_BYTES,
    MAX_OPUS_PACKET_BYTES,
    AbortEvent,
    ClientEvent,
    ListenEvent,
    McpEvent,
    ProtocolViolationError,
    SystemEvent,
    assistant_sleep_payload,
    listen_started_payload,
    llm_payload,
    mcp_payload,
    parse_client_event,
    parse_device_hello,
    server_hello_payload,
    stt_payload,
    tts_payload,
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class _PacedAudioStream:
    generation: int
    queue: asyncio.Queue[bytes | None]
    cancelled: asyncio.Event
    task: asyncio.Task[None] | None = None


class WebSocketConversationSink:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        session_id: str,
        telemetry: ConversationTelemetry | None = None,
        output_sample_rate: int = 24_000,
        frame_duration_ms: int = 60,
    ) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._telemetry = telemetry or NullConversationTelemetry()
        self._output_sample_rate = output_sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._encoder = OpusEncoder(output_sample_rate)
        self._lock = asyncio.Lock()
        self._wire_lock = asyncio.Lock()
        self._cancel_generation = 0
        self._tts_generation: int | None = None
        self._audio_stream: _PacedAudioStream | None = None
        self._pending_pcm = bytearray()
        self._prebuffer_frames = 3
        self._queue_frames = 12

    async def emit(self, output: ConversationOutput) -> None:
        if output.generation < self._cancel_generation:
            return
        event_type = {
            OutputKind.TTS_START: "tts.start",
            OutputKind.TTS_STOP: "tts.stop",
        }.get(output.kind, output.kind.value)
        if output.kind in {
            OutputKind.ADMISSION,
            OutputKind.PLAN,
            OutputKind.TTS_START,
            OutputKind.TTS_STOP,
            OutputKind.ERROR,
        }:
            self._telemetry.record(
                self._session_id,
                event_type,
                generation=output.generation,
                turn_id=output.turn_id,
                payload=output.payload,
            )
        if output.kind is OutputKind.TTS_START:
            await self._start_tts(output.generation)
            return
        if output.kind is OutputKind.AUDIO and output.audio is not None:
            await self._send_audio(output)
            return
        if output.kind is OutputKind.TTS_STOP:
            await self._stop_tts(output.generation, flush=True)
            return
        async with self._lock:
            if output.generation < self._cancel_generation:
                return
            payload = self._json_output(output)
            if payload is not None:
                await self._send_text(payload)

    async def send_control(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            await self._send_text({"session_id": self._session_id, **payload})

    async def send_stt(self, transcript: Transcript, *, generation: int = 0) -> None:
        self._telemetry.record(
            self._session_id,
            "stt.final",
            generation=max(generation, self._cancel_generation),
            payload={
                "locale": transcript.locale,
                "character_count": len(transcript.text),
                "confidence": transcript.confidence,
                "stability": transcript.stability,
            },
        )
        async with self._lock:
            await self._send_text(stt_payload(self._session_id, transcript.text))

    async def send_listening(
        self, source: WakeSource | None = None, *, generation: int = 0
    ) -> None:
        self._telemetry.record(
            self._session_id,
            "listen.start",
            generation=max(generation, self._cancel_generation),
            payload={"source": source.value if source is not None else "turn_continuation"},
        )
        async with self._lock:
            await self._send_text(
                listen_started_payload(
                    self._session_id, source=source.value if source is not None else None
                )
            )

    async def send_assistant_sleep(self, reason: str, *, generation: int = 0) -> None:
        self._telemetry.record(
            self._session_id,
            "assistant.sleep",
            generation=max(generation, self._cancel_generation),
            payload={"reason": reason},
        )
        async with self._lock:
            await self._send_text(assistant_sleep_payload(self._session_id, reason))

    async def send_mcp(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            await self._send_text(mcp_payload(self._session_id, payload))

    async def send_hello(self) -> None:
        async with self._lock:
            payload = server_hello_payload(
                self._session_id,
                sample_rate=self._output_sample_rate,
                frame_duration=self._frame_duration_ms,
            )
            await self._send_text(payload)

    def mark_cancelled(self, generation: int) -> None:
        self._cancel_generation = max(self._cancel_generation, generation)

    async def cancel_tts(self, generation: int) -> None:
        self.mark_cancelled(generation)
        async with self._lock:
            stream = self._detach_tts()
        await self._cancel_stream(stream)
        if stream is not None:
            await self._send_text(tts_payload(self._session_id, "stop"))

    async def _start_tts(self, generation: int) -> None:
        async with self._lock:
            if generation < self._cancel_generation:
                return
            previous = self._detach_tts()
        await self._cancel_stream(previous)
        if previous is not None:
            await self._send_text(tts_payload(self._session_id, "stop"))
        async with self._lock:
            if generation < self._cancel_generation:
                return
            stream = _PacedAudioStream(
                generation=generation,
                queue=asyncio.Queue(maxsize=self._queue_frames),
                cancelled=asyncio.Event(),
            )
            self._tts_generation = generation
            self._audio_stream = stream
            await self._send_text(tts_payload(self._session_id, "start"))
            stream.task = asyncio.create_task(self._run_paced_audio(stream))

    async def _send_audio(self, output: ConversationOutput) -> None:
        if output.audio is None or output.audio.encoding != "pcm_s16le":
            return
        pcm = output.audio.data
        if output.audio.sample_rate != self._output_sample_rate:
            pcm = await asyncio.to_thread(
                self._resample_pcm,
                pcm,
                output.audio.sample_rate,
                self._output_sample_rate,
            )
        async with self._lock:
            stream = self._audio_stream
            if (
                stream is None
                or stream.generation != output.generation
                or output.generation < self._cancel_generation
            ):
                return
            self._pending_pcm.extend(pcm)
            packets = self._encode_ready_frames()
        for packet in packets:
            if not await self._enqueue_audio(stream, packet):
                return

    async def _stop_tts(self, generation: int, *, flush: bool) -> None:
        async with self._lock:
            stream = self._audio_stream
            if stream is None or stream.generation != generation:
                return
            final_packet = self._encode_final_frame() if flush else None
        if final_packet is not None and not await self._enqueue_audio(stream, final_packet):
            return
        if not await self._enqueue_audio(stream, None):
            return
        if stream.task is not None:
            await asyncio.gather(stream.task, return_exceptions=True)
        async with self._lock:
            if (
                self._audio_stream is not stream
                or generation < self._cancel_generation
                or stream.cancelled.is_set()
            ):
                return
            await self._send_text(tts_payload(self._session_id, "stop"))
            self._discard_tts()

    def _encode_ready_frames(self) -> list[bytes]:
        frame_samples = self._output_sample_rate * self._frame_duration_ms // 1000
        frame_bytes = frame_samples * 2
        packets: list[bytes] = []
        while len(self._pending_pcm) >= frame_bytes:
            frame = bytes(self._pending_pcm[:frame_bytes])
            del self._pending_pcm[:frame_bytes]
            packets.append(self._encoder.encode(frame, frame_samples=frame_samples))
        return packets

    def _encode_final_frame(self) -> bytes | None:
        if not self._pending_pcm:
            return None
        frame_samples = self._output_sample_rate * self._frame_duration_ms // 1000
        frame_bytes = frame_samples * 2
        frame = bytes(self._pending_pcm)
        self._pending_pcm.clear()
        frame += b"\0" * (frame_bytes - len(frame))
        return self._encoder.encode(frame, frame_samples=frame_samples)

    async def _enqueue_audio(self, stream: _PacedAudioStream, packet: bytes | None) -> bool:
        if stream.cancelled.is_set():
            return False
        put_task = asyncio.create_task(stream.queue.put(packet))
        cancelled = asyncio.create_task(stream.cancelled.wait())
        done, _ = await asyncio.wait({put_task, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            put_task.cancel()
            await asyncio.gather(put_task, return_exceptions=True)
            return False
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        return True

    async def _run_paced_audio(self, stream: _PacedAudioStream) -> None:
        loop = asyncio.get_running_loop()
        sequence = 0
        next_packet_at = 0.0
        while not stream.cancelled.is_set():
            packet = await stream.queue.get()
            if packet is None:
                return
            if sequence >= self._prebuffer_frames:
                delay = next_packet_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_packet_at = max(next_packet_at, loop.time()) + (self._frame_duration_ms / 1000)
            elif sequence == self._prebuffer_frames - 1:
                next_packet_at = loop.time() + self._frame_duration_ms / 1000
            if (
                stream.cancelled.is_set()
                or stream.generation < self._cancel_generation
                or self._audio_stream is not stream
            ):
                return
            await self._send_bytes(packet)
            sequence += 1

    async def _cancel_stream(self, stream: _PacedAudioStream | None) -> None:
        if stream is None:
            return
        stream.cancelled.set()
        if stream.task is not None and not stream.task.done():
            stream.task.cancel()
            await asyncio.gather(stream.task, return_exceptions=True)

    def _detach_tts(self) -> _PacedAudioStream | None:
        stream = self._audio_stream
        if stream is not None:
            stream.cancelled.set()
        self._discard_tts()
        return stream

    async def _send_bytes(self, packet: bytes) -> None:
        async with self._wire_lock:
            await self._websocket.send_bytes(packet)

    async def _send_text(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_CONTROL_FRAME_BYTES:
            raise ValueError("control frame exceeds 8 KiB wire limit")
        async with self._wire_lock:
            await self._websocket.send_text(encoded)

    def _discard_tts(self) -> None:
        self._tts_generation = None
        self._audio_stream = None
        self._pending_pcm.clear()

    @staticmethod
    def _resample_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
        import numpy as np

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        converted = soxr.resample(samples, source_rate, target_rate, quality="HQ")
        return bytes((np.clip(converted, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())

    def _json_output(self, output: ConversationOutput) -> dict[str, Any] | None:
        if output.kind is OutputKind.ADMISSION:
            disposition = output.payload.get("disposition")
            if disposition in {
                AdmissionDisposition.NON_ACTIONABLE.value,
                AdmissionDisposition.NOT_ADDRESSED.value,
                AdmissionDisposition.UNCLEAR.value,
                AdmissionDisposition.INTERRUPT.value,
            }:
                return listen_started_payload(self._session_id)
            return llm_payload(self._session_id, "thinking")
        if output.kind is OutputKind.TEXT_DELTA:
            return llm_payload(
                self._session_id,
                "neutral",
                text=str(output.payload.get("text", "")),
            )
        if output.kind is OutputKind.ERROR:
            return llm_payload(
                self._session_id,
                "sad",
                text=str(output.payload.get("code", "conversation_failed")),
            )
        return None

    def close(self) -> None:
        self._encoder.close()


class VoiceSession:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        settings: Settings,
        profile: SessionProfile,
        asr: SherpaZipformerAsrProvider,
        vad_model: SileroVadModel,
        tts: TtsProvider,
        engine_factory: Callable[
            [TurnArbiter, WebSocketConversationSink, SessionProfile, ToolBroker],
            ConversationEngine,
        ],
        telemetry: ConversationTelemetry | None = None,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.profile = profile
        self.session_id = uuid4().hex
        self.arbiter = TurnArbiter(self.session_id)
        self.sink = WebSocketConversationSink(
            websocket,
            session_id=self.session_id,
            telemetry=telemetry,
            output_sample_rate=settings.wire_sample_rate,
            frame_duration_ms=settings.wire_frame_duration_ms,
        )
        self.asr = asr
        self.tts = tts
        self.vad = SileroVadSession(
            vad_model,
            threshold=settings.vad_threshold,
            release_threshold=settings.vad_release_threshold,
            min_silence_ms=settings.vad_min_silence_ms,
            max_speech_seconds=settings.max_utterance_seconds,
        )
        self.mcp = DeviceMcpClient(self.sink.send_mcp, session_id=self.session_id)
        self.engine = engine_factory(self.arbiter, self.sink, profile, self.mcp)
        self.inactivity = InactivityController(
            arbiter=self.arbiter,
            first_input_seconds=profile.policy.first_input_seconds,
            between_turns_seconds=profile.policy.between_turns_seconds,
            closing_grace_seconds=profile.policy.closing_grace_seconds,
            max_session_seconds=profile.policy.max_session_seconds,
            goodbye=self._goodbye,
        )
        self._decoder = OpusDecoder(settings.input_sample_rate)
        self._speech = bytearray()
        self._pre_roll = bytearray()
        self._pre_roll_bytes = settings.input_sample_rate * settings.vad_pre_roll_ms // 1000 * 2
        self._speech_active = False
        self._asr_task: asyncio.Task[None] | None = None
        self._asr_context: OperationContext | None = None
        self._mcp_bootstrap_task: asyncio.Task[None] | None = None
        self.mcp_ready = asyncio.Event()
        self._closed = False
        self._telemetry = telemetry or NullConversationTelemetry()

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            hello_message = await asyncio.wait_for(
                self.websocket.receive(), timeout=self.settings.hello_timeout_seconds
            )
            if hello_message.get("type") == "websocket.disconnect":
                return
            hello_text = hello_message.get("text")
            if not isinstance(hello_text, str):
                raise ProtocolViolationError("device hello must be a text frame")
            hello = parse_device_hello(
                hello_text,
                expected_sample_rate=self.settings.input_sample_rate,
                expected_frame_duration=self.settings.input_frame_duration_ms,
            )
            await self.sink.send_hello()
            if hello.features.mcp:
                self._mcp_bootstrap_task = asyncio.create_task(self._initialize_mcp())

            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_audio(message["bytes"])
                elif message.get("text") is not None:
                    event = parse_client_event(message["text"], session_id=self.session_id)
                    await self._handle_control(event)
        except TimeoutError:
            await self.websocket.close(code=1002, reason="device hello timeout")
        except ProtocolViolationError as error:
            await self.websocket.close(code=error.close_code, reason=error.reason)
        except WebSocketDisconnect:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._mcp_bootstrap_task is not None:
            self._mcp_bootstrap_task.cancel()
            await asyncio.gather(self._mcp_bootstrap_task, return_exceptions=True)
            self._mcp_bootstrap_task = None
        await self.mcp.close()
        await self._cancel_asr()
        await self.inactivity.close()
        await self.arbiter.abort("socket_closed")
        self._decoder.close()
        self.sink.close()
        await self._telemetry.close()

    async def _handle_control(self, event: ClientEvent) -> None:
        if isinstance(event, ListenEvent) and event.state in {"start", "detect"}:
            source = WakeSource(event.source or "button")
            if self.arbiter.snapshot.state is ConversationState.CLOSING:
                await self.inactivity.wake_during_closing(source)
            else:
                await self.inactivity.assistant_opened(source)
            await self._cancel_asr()
            self._reset_input()
            await self.sink.cancel_tts(self.arbiter.snapshot.generation)
            await self.sink.send_listening(
                source, generation=self.arbiter.snapshot.generation
            )
            return
        if isinstance(event, ListenEvent) and event.state == "stop":
            await self._close_assistant(event.reason or "listen_stop")
            return
        if isinstance(event, AbortEvent):
            await self._abort_current(event.reason)
            return
        if isinstance(event, SystemEvent):
            await self._close_assistant(event.reason or "system_sleep")
            return
        if isinstance(event, McpEvent):
            try:
                await self.mcp.handle_payload(event.payload)
            except DeviceMcpError as error:
                raise ProtocolViolationError("invalid MCP payload") from error
            return

    async def _initialize_mcp(self) -> None:
        try:
            await self.mcp.initialize()
            self.mcp_ready.set()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "device_mcp_bootstrap_failed",
                session_id=self.session_id,
                error=type(error).__name__,
            )

    async def _abort_current(self, reason: str) -> None:
        was_closing = self.arbiter.snapshot.state is ConversationState.CLOSING
        receipt = await self.arbiter.abort(reason)
        self._telemetry.record(
            self.session_id,
            "abort",
            generation=receipt.generation,
            payload={"reason": reason},
        )
        self.sink.mark_cancelled(receipt.generation)
        await self._cancel_asr()
        self._reset_input()
        await self.sink.cancel_tts(receipt.generation)
        if was_closing:
            await self.inactivity.interrupt_closing()
        await self.arbiter.finish_cancellation(receipt)
        if self.arbiter.snapshot.state is ConversationState.LISTENING:
            await self.sink.send_listening(generation=self.arbiter.snapshot.generation)
            await self.inactivity.turn_completed()

    async def _close_assistant(self, reason: str) -> None:
        await self.inactivity.assistant_closed(reason)
        snapshot = self.arbiter.snapshot
        self.sink.mark_cancelled(snapshot.generation)
        await self._cancel_asr()
        self._reset_input()
        await self.sink.cancel_tts(snapshot.generation)

    async def _handle_audio(self, packet: bytes) -> None:
        if len(packet) > MAX_OPUS_PACKET_BYTES:
            raise ProtocolViolationError("Opus packet too large", close_code=1009)
        try:
            pcm = self._decoder.decode(packet)
        except (OpusError, ValueError) as error:
            raise ProtocolViolationError("invalid Opus packet") from error
        if not pcm:
            return
        if self.arbiter.snapshot.state is not ConversationState.LISTENING:
            return
        if self._asr_task is not None and not self._asr_task.done():
            return
        results = self.vad.process(pcm)
        started = any(item.speech_started for item in results)
        ended = any(item.speech_ended for item in results)

        if self._speech_active:
            if not self._append_speech(pcm):
                self._telemetry.record(
                    self.session_id,
                    "vad.forced_finalize",
                    generation=self.arbiter.snapshot.generation,
                    payload={"reason": "pcm_buffer_limit"},
                )
                self._start_asr()
                return
        else:
            self._pre_roll.extend(pcm)
            if len(self._pre_roll) > self._pre_roll_bytes:
                del self._pre_roll[: len(self._pre_roll) - self._pre_roll_bytes]
            if started:
                await self.inactivity.candidate_started()
                self._speech_active = True
                if not self._append_speech(self._pre_roll or pcm):
                    self._telemetry.record(
                        self.session_id,
                        "vad.forced_finalize",
                        generation=self.arbiter.snapshot.generation,
                        payload={"reason": "pcm_buffer_limit"},
                    )
                    self._start_asr()
                    return
                self._pre_roll.clear()
        if ended and self._speech_active:
            self._start_asr()

    def _append_speech(self, pcm: bytes | bytearray) -> bool:
        remaining = self.settings.max_utterance_buffer_bytes - len(self._speech)
        if remaining <= 0:
            return False
        self._speech.extend(pcm[:remaining])
        return len(pcm) <= remaining

    def _start_asr(self) -> None:
        if self._asr_task and not self._asr_task.done():
            return
        audio = bytes(self._speech)
        self._speech.clear()
        self._pre_roll.clear()
        self._speech_active = False
        self.vad.reset()
        if not audio:
            return
        self._asr_task = asyncio.create_task(self._transcribe(audio))

    async def _transcribe(self, pcm: bytes) -> None:
        context = OperationContext(
            self.session_id,
            f"asr:{uuid4().hex}",
            self.arbiter.snapshot.generation,
            CancellationToken(),
            monotonic() + self.settings.asr_seconds,
        )
        self._asr_context = context
        try:
            transcript = await self.asr.transcribe_pcm(
                pcm,
                sample_rate=self.settings.input_sample_rate,
                locale=self.profile.locale,
                context=context,
            )
            if not transcript.text:
                await self.inactivity.candidate_rejected()
                return
            normalized = Transcript(
                transcript.text,
                transcript.locale,
                transcript.confidence,
                transcript.stability,
            )
            await self.sink.send_stt(
                normalized, generation=self.arbiter.snapshot.generation
            )
            disposition = await self.engine.handle_transcript(normalized)
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
                await self.sink.send_listening(generation=self.arbiter.snapshot.generation)
            elif state is ConversationState.STANDBY:
                await self.sink.send_assistant_sleep(
                    "semantic_end", generation=self.arbiter.snapshot.generation
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.inactivity.candidate_rejected()
            self._telemetry.record(
                self.session_id,
                "error",
                generation=self.arbiter.snapshot.generation,
                payload={
                    "code": "transcription_or_turn_failed",
                    "stage": "asr_turn",
                    "error_type": type(error).__name__,
                },
            )
            await self.sink.send_control(
                {"type": "llm", "emotion": "sad", "text": type(error).__name__}
            )
            if self.arbiter.snapshot.state is ConversationState.LISTENING:
                await self.sink.send_listening(generation=self.arbiter.snapshot.generation)
        finally:
            if self._asr_task is asyncio.current_task():
                self._asr_task = None
            if self._asr_context is context:
                self._asr_context = None

    async def _goodbye(self, reason: str) -> None:
        token = CancellationToken()
        context = OperationContext(
            self.session_id,
            f"goodbye:{uuid4().hex}",
            self.arbiter.snapshot.generation,
            token,
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
                        ConversationOutput(
                            kind=OutputKind.TTS_START,
                            turn_id=None,
                            generation=context.generation,
                        )
                    )
                    started = True
                await self.sink.emit(
                    ConversationOutput(
                        kind=OutputKind.AUDIO,
                        turn_id=None,
                        generation=context.generation,
                        payload={"system": "goodbye", "reason": reason},
                        audio=audio,
                    )
                )
        except Exception as error:
            # A failed goodbye must not leave the assistant gate stuck open. Task
            # cancellation still propagates because asyncio.CancelledError is a BaseException.
            self._telemetry.record(
                self.session_id,
                "error",
                generation=context.generation,
                payload={
                    "code": "goodbye_tts_failed",
                    "stage": "goodbye_tts",
                    "error_type": type(error).__name__,
                },
            )
            logger.warning(
                "goodbye_tts_failed",
                session_id=self.session_id,
                error=type(error).__name__,
            )
        finally:
            if started:
                await self.sink.emit(
                    ConversationOutput(
                        kind=OutputKind.TTS_STOP,
                        turn_id=None,
                        generation=context.generation,
                    )
                )
        await self.sink.send_assistant_sleep(
            reason, generation=self.arbiter.snapshot.generation
        )

    async def _cancel_asr(self) -> None:
        task = self._asr_task
        self._asr_task = None
        context = self._asr_context
        self._asr_context = None
        if context is not None:
            context.token.cancel("asr_cancelled")
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _reset_input(self) -> None:
        self._speech.clear()
        self._pre_roll.clear()
        self._speech_active = False
        self.vad.reset()
