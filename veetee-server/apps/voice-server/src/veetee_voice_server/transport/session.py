from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from time import monotonic
from typing import Any
from uuid import uuid4

import soxr  # type: ignore[import-untyped]
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
    ConversationOutput,
    OutputKind,
    Transcript,
    WakeSource,
)
from veetee_voice_server.manager import SessionProfile
from veetee_voice_server.providers.contracts import TtsProvider
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.silero_vad import SileroVadModel, SileroVadSession
from veetee_voice_server.transport.opus import OpusDecoder, OpusEncoder, OpusError
from veetee_voice_server.transport.protocol import (
    MAX_OPUS_PACKET_BYTES,
    AbortEvent,
    ClientEvent,
    ListenEvent,
    McpEvent,
    ProtocolViolationError,
    SystemEvent,
    parse_client_event,
    parse_device_hello,
    server_hello_payload,
)


class WebSocketConversationSink:
    def __init__(
        self,
        websocket: WebSocket,
        *,
        session_id: str,
        output_sample_rate: int = 24_000,
        frame_duration_ms: int = 60,
    ) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._output_sample_rate = output_sample_rate
        self._frame_duration_ms = frame_duration_ms
        self._encoder = OpusEncoder(output_sample_rate)
        self._lock = asyncio.Lock()
        self._cancel_generation = 0

    async def emit(self, output: ConversationOutput) -> None:
        async with self._lock:
            if output.kind.value == "audio" and output.audio is not None:
                if output.generation < self._cancel_generation:
                    return
                pcm = output.audio.data
                if output.audio.sample_rate != self._output_sample_rate:
                    pcm = await asyncio.to_thread(
                        self._resample_pcm, pcm, output.audio.sample_rate, self._output_sample_rate
                    )
                frame_samples = self._output_sample_rate * self._frame_duration_ms // 1000
                for offset in range(0, len(pcm), frame_samples * 2):
                    frame = pcm[offset : offset + frame_samples * 2]
                    if len(frame) < frame_samples * 2:
                        frame += b"\0" * (frame_samples * 2 - len(frame))
                    await self._websocket.send_bytes(
                        self._encoder.encode(frame, frame_samples=frame_samples)
                    )
                return
            await self._websocket.send_text(
                json.dumps(self._json_output(output), ensure_ascii=False, separators=(",", ":"))
            )

    async def send_control(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            payload = {"session_id": self._session_id, **payload}
            await self._websocket.send_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

    async def send_hello(self) -> None:
        async with self._lock:
            payload = server_hello_payload(
                self._session_id,
                sample_rate=self._output_sample_rate,
                frame_duration=self._frame_duration_ms,
            )
            await self._websocket.send_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )

    def mark_cancelled(self, generation: int) -> None:
        self._cancel_generation = max(self._cancel_generation, generation)

    @staticmethod
    def _resample_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
        import numpy as np

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        converted = soxr.resample(samples, source_rate, target_rate, quality="HQ")
        return bytes((np.clip(converted, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())

    def _json_output(self, output: ConversationOutput) -> dict[str, Any]:
        payload = {
            "session_id": self._session_id,
            "type": output.kind.value,
            "generation": output.generation,
            **output.payload,
        }
        if output.turn_id is not None:
            payload["turn_id"] = output.turn_id
        return payload

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
            [TurnArbiter, WebSocketConversationSink, SessionProfile], ConversationEngine
        ],
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.profile = profile
        self.session_id = uuid4().hex
        self.arbiter = TurnArbiter(self.session_id)
        self.sink = WebSocketConversationSink(
            websocket,
            session_id=self.session_id,
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
        self.engine = engine_factory(self.arbiter, self.sink, profile)
        self.inactivity = InactivityController(
            arbiter=self.arbiter,
            first_input_seconds=profile.policy.first_input_seconds,
            between_turns_seconds=profile.policy.between_turns_seconds,
            closing_grace_seconds=profile.policy.closing_grace_seconds,
            goodbye=self._goodbye,
        )
        self._decoder = OpusDecoder(settings.input_sample_rate)
        self._speech = bytearray()
        self._asr_task: asyncio.Task[None] | None = None
        self._closed = False

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
            parse_device_hello(
                hello_text,
                expected_sample_rate=self.settings.input_sample_rate,
                expected_frame_duration=self.settings.input_frame_duration_ms,
            )
            await self.sink.send_hello()

            while True:
                message = await self.websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._handle_audio(message["bytes"])
                elif message.get("text") is not None:
                    event = parse_client_event(
                        message["text"], session_id=self.session_id
                    )
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
        if self._asr_task and not self._asr_task.done():
            self._asr_task.cancel()
            await asyncio.gather(self._asr_task, return_exceptions=True)
        await self.inactivity.close()
        await self.arbiter.abort("socket_closed")
        self._decoder.close()
        self.sink.close()

    async def _handle_control(self, event: ClientEvent) -> None:
        if isinstance(event, ListenEvent) and event.state in {"start", "detect"}:
            source = WakeSource(event.source or "button")
            if self.arbiter.snapshot.state is ConversationState.CLOSING:
                await self.inactivity.wake_during_closing(source)
            else:
                await self.inactivity.assistant_opened(source)
            await self.sink.send_control(
                {"type": "listen", "state": "start", "source": source.value}
            )
            return
        if isinstance(event, ListenEvent) and event.state == "stop":
            await self._abort_or_close(event.reason or "listen_stop")
            return
        if isinstance(event, AbortEvent):
            await self._abort_or_close(event.reason)
            return
        if isinstance(event, SystemEvent):
            await self._abort_or_close(event.reason or "system_sleep")
            return
        if isinstance(event, McpEvent):
            # MCP dispatch is introduced as its own cancellation-scoped vertical slice.
            return

    async def _abort_or_close(self, reason: str) -> None:
        state = self.arbiter.snapshot.state
        if state in {
            ConversationState.THINKING,
            ConversationState.SPEAKING,
            ConversationState.CANCELLING,
        }:
            receipt = await self.arbiter.abort(reason)
            self.sink.mark_cancelled(receipt.generation)
            await self.arbiter.finish_cancellation(receipt)
        else:
            await self.arbiter.close_assistant(reason)

    async def _handle_audio(self, packet: bytes) -> None:
        if len(packet) > MAX_OPUS_PACKET_BYTES:
            raise ProtocolViolationError("Opus packet too large", close_code=1009)
        try:
            pcm = self._decoder.decode(packet)
        except (OpusError, ValueError) as error:
            raise ProtocolViolationError("invalid Opus packet") from error
        if not pcm:
            return
        state = self.arbiter.snapshot.state
        if state is ConversationState.STANDBY:
            return
        if state is ConversationState.SPEAKING:
            results = self.vad.process(pcm)
            if any(item.speech_started for item in results):
                await self._abort_or_close("voice_interrupt")
                await self.inactivity.assistant_opened(WakeSource.WAKE_WORD)
            return
        self._speech.extend(pcm)
        results = self.vad.process(pcm)
        if any(item.speech_started for item in results) and state is ConversationState.THINKING:
            await self._abort_or_close("voice_interrupt")
            await self.inactivity.assistant_opened(WakeSource.WAKE_WORD)
        if any(item.speech_ended for item in results):
            self._start_asr()

    def _start_asr(self) -> None:
        if self._asr_task and not self._asr_task.done():
            return
        audio = bytes(self._speech)
        self._speech.clear()
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
        try:
            transcript = await self.asr.transcribe_pcm(
                pcm,
                sample_rate=self.settings.input_sample_rate,
                locale=self.profile.locale,
                context=context,
            )
            if transcript.text:
                await self.inactivity.valid_user_activity()
            await self.engine.handle_transcript(
                Transcript(
                    transcript.text, transcript.locale, transcript.confidence, transcript.stability
                )
            )
            await self.inactivity.turn_completed()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.sink.send_control(
                {"type": "error", "code": "asr_failed", "detail": type(error).__name__}
            )

    async def _goodbye(self, reason: str) -> None:
        token = CancellationToken()
        context = OperationContext(
            self.session_id,
            f"goodbye:{uuid4().hex}",
            self.arbiter.snapshot.generation,
            token,
            monotonic() + self.profile.policy.closing_grace_seconds,
        )
        async for audio in iterate_operation(
            self.tts.synthesize(self.profile.goodbye_text, self.profile.locale, context),
            context,
        ):
            await self.sink.emit(
                ConversationOutput(
                    kind=OutputKind.AUDIO,
                    turn_id=None,
                    generation=context.generation,
                    payload={"system": "goodbye", "reason": reason},
                    audio=audio,
                )
            )
        await self.sink.send_control(
            {"type": "system", "command": "assistant_sleep", "reason": reason}
        )
