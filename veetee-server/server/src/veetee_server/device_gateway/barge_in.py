"""Full-duplex VAD detector isolated from conversation-turn VAD state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from veetee_server.audio.codec import AudioDecoder
from veetee_server.audio.queue import AudioQueueItem
from veetee_server.domain.session import ConversationTurn, DeviceSession
from veetee_server.pipeline.events import TtsStopEvent
from veetee_server.pipeline.vad import BaseVADStream, VadEvent, VadEventKind

from .downlink import GatewayEventSink


@dataclass(frozen=True, slots=True)
class BargeInDetection:
    frames: tuple[AudioQueueItem, ...]


class SyncVADStream(Protocol):
    """Minimal synchronous VAD contract used by deterministic harnesses."""

    def process_frame(self, pcm_data: bytes) -> VadEvent: ...

    def reset(self) -> None: ...


class BargeInDetector:
    """Keeps bounded packet pre-roll while looking for a speech-start event."""

    def __init__(
        self,
        decoder: AudioDecoder,
        vad: BaseVADStream | SyncVADStream,
        *,
        max_pre_roll_frames: int = 5,
    ) -> None:
        if max_pre_roll_frames <= 0:
            raise ValueError("max_pre_roll_frames must be positive")
        self._decoder = decoder
        self._vad = vad
        self._frames: deque[AudioQueueItem] = deque(maxlen=max_pre_roll_frames)
        self._triggered = False

    async def process(self, item: AudioQueueItem) -> BargeInDetection | None:
        if self._triggered:
            return None
        self._frames.append(item)
        pcm = self._decoder.decode(item.payload)
        if isinstance(self._vad, BaseVADStream):
            events = await self._vad.process_pcm_async(pcm)
        else:
            events = [self._vad.process_frame(pcm)]
        if any(event.kind is VadEventKind.SPEECH_START for event in events):
            self._triggered = True
            return BargeInDetection(tuple(self._frames))
        return None

    def reset(self) -> None:
        self._frames.clear()
        self._triggered = False
        self._vad.reset()

    def close(self) -> None:
        close = getattr(self._decoder, "close", None)
        if close is not None:
            close()


class BargeInCoordinator:
    """Applies a detected interruption as one guarded session transition."""

    def __init__(self, session: DeviceSession, detector: BargeInDetector) -> None:
        self._session = session
        self._detector = detector

    async def process(self, item: AudioQueueItem, expected_turn: ConversationTurn) -> bool:
        detection = await self._detector.process(item)
        if detection is None:
            return False

        session = self._session
        if await session.begin_barge_in(expected_turn) is None:
            return False

        await GatewayEventSink(session).emit(TtsStopEvent(session_id=str(session.id)))
        generation = session.ingress_queue.generation
        for frame in detection.frames:
            await session.ingress_queue.put(
                AudioQueueItem(
                    payload=frame.payload,
                    duration_ms=frame.duration_ms,
                    generation=generation,
                    timestamp_ms=frame.timestamp_ms,
                )
            )
        return True

    def close(self) -> None:
        self._detector.close()


class AutoEndpointDetector:
    """Observes auto-mode ingress and triggers once when speech ends."""

    def __init__(self, decoder: AudioDecoder, vad: BaseVADStream | SyncVADStream) -> None:
        self._decoder = decoder
        self._vad = vad
        self._triggered = False
        self._speech_started = False

    @property
    def speech_started(self) -> bool:
        return self._speech_started

    async def process(self, item: AudioQueueItem) -> bool:
        if self._triggered:
            return False
        pcm = self._decoder.decode(item.payload)
        if isinstance(self._vad, BaseVADStream):
            events = await self._vad.process_pcm_async(pcm)
        else:
            events = [self._vad.process_frame(pcm)]
        if any(event.kind is VadEventKind.SPEECH_START for event in events):
            self._speech_started = True
        if self._speech_started and any(
            event.kind is VadEventKind.SPEECH_END for event in events
        ):
            self._triggered = True
            return True
        return False

    def close(self) -> None:
        close = getattr(self._decoder, "close", None)
        if close is not None:
            close()
