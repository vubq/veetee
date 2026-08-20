"""Fake AI pipeline orchestrator for the M1.6 milestone.

The orchestrator drives the deterministic VAD -> ASR -> LLM -> TTS chain for
one capture turn:

1. drain the session's ingress queue (all frames captured during LISTENING);
2. run the VAD over the decoded frames to extract a single speech segment;
3. transcribe (ASR), segment into sentences (LLM), and stream TTS chunks;
4. emit typed events through an injected ``EventSink``.

The pipeline is turn-scoped: it is started from a processing turn's
cancellation scope, so an abort or a new (barge-in) turn cancels it at the
next await point. Every ``emit``/``await`` is preceded by an ``_alive`` check,
and stale-generation protection is enforced once streaming begins.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

from veetee_server.audio.codec import AudioDecoder, AudioEncoder
from veetee_server.domain.errors import InvalidTransitionError, StaleGenerationError
from veetee_server.domain.session import (
    ConversationTurn,
    DeviceSession,
    Generation,
    SessionState,
    TurnState,
)

from .asr import FakeASR
from .events import (
    EventSink,
    SttEvent,
    TtsChunkEvent,
    TtsSentenceStartEvent,
    TtsStartEvent,
    TtsStopEvent,
)
from .framing import build_downlink_frame
from .llm import FakeLLM
from .tts import FakeTTS
from .vad import FakeVAD

logger = logging.getLogger("veetee.pipeline")

_DOWNLINK_FRAME_DURATION_MS = 60.0


class PipelineOutcome(StrEnum):
    """Terminal outcomes of a pipeline run."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_UTTERANCE = "no_utterance"
    INVALID_START = "invalid_start"


class FakePipeline:
    """Turn-scoped orchestrator over deterministic fake AI components."""

    def __init__(
        self,
        *,
        decoder: AudioDecoder,
        encoder: AudioEncoder,
        protocol_version: int,
        vad: FakeVAD,
        asr: FakeASR,
        llm: FakeLLM,
        tts: FakeTTS,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        if protocol_version not in (1, 2, 3):
            raise ValueError(f"Unsupported protocol version: {protocol_version}")
        self.decoder = decoder
        self.encoder = encoder
        self.protocol_version = protocol_version
        self.vad = vad
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self._now_ms = now_ms

    async def run(self, session: DeviceSession, sink: EventSink) -> PipelineOutcome:
        """Runs the pipeline for the session's current processing turn.

        Returns a ``PipelineOutcome`` string. May raise
        ``SlowClientQueueOverflowError`` when the downlink queue overflows
        under the fail-session policy; the gateway supervisor handles that as
        a slow-client close.
        """
        turn = session.current_turn
        if turn is None or turn.state is not TurnState.PROCESSING:
            return PipelineOutcome.INVALID_START

        expected_queue_gen = session.egress_queue.generation

        utterance = self._collect_utterance(session, turn)
        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED
        if utterance is None:
            return PipelineOutcome.NO_UTTERANCE

        transcript = self.asr.transcribe(utterance)
        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED
        await sink.emit(SttEvent(text=transcript, session_id=str(session.id)))
        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED

        try:
            generation = session.begin_streaming()
        except InvalidTransitionError:
            return PipelineOutcome.CANCELLED

        if not self._alive(
            session, turn, generation, expected_queue_generation=expected_queue_gen
        ):
            return PipelineOutcome.CANCELLED
        await sink.emit(TtsStartEvent(session_id=str(session.id)))

        sentence_id = 0
        for sentence in self.llm.segments(transcript):
            if not self._alive(
                session, turn, generation, expected_queue_generation=expected_queue_gen
            ):
                return PipelineOutcome.CANCELLED
            sentence_id += 1
            await sink.emit(
                TtsSentenceStartEvent(
                    text=sentence,
                    sentence_id=sentence_id,
                    session_id=str(session.id),
                )
            )
            async for pcm in self.tts.synthesize(sentence):
                if not self._alive(
                    session, turn, generation, expected_queue_generation=expected_queue_gen
                ):
                    return PipelineOutcome.CANCELLED
                opus = self.encoder.encode(pcm)
                frame = build_downlink_frame(self.protocol_version, opus, now_ms=self._now_ms)
                await sink.emit(
                    TtsChunkEvent(
                        pcm=pcm,
                        frame=frame,
                        duration_ms=_DOWNLINK_FRAME_DURATION_MS,
                        session_id=str(session.id),
                    )
                )

        if not self._alive(
            session, turn, generation, expected_queue_generation=expected_queue_gen
        ):
            return PipelineOutcome.CANCELLED
        await sink.emit(TtsStopEvent(session_id=str(session.id)))
        try:
            session.complete_turn()
        except InvalidTransitionError:
            return PipelineOutcome.CANCELLED
        return PipelineOutcome.COMPLETED

    def _collect_utterance(self, session: DeviceSession, turn: ConversationTurn) -> bytes | None:
        """Drains captured frames and returns the VAD speech segment PCM.

        Synchronous by design: after ``listen/stop`` the gateway drops any
        further audio, so the whole utterance is already buffered. Returning
        ``None`` means no usable speech was found.
        """
        items = session.ingress_queue.drain()
        if not items:
            return None
        self.vad.reset()
        frames: list[bytes] = []
        for item in items:
            frames.append(self.decoder.decode(item.payload))
            self.vad.process_frame(frames[-1])
        segment = self.vad.finish()
        if segment is None or segment.end_frame_index <= segment.start_frame_index:
            return None
        return b"".join(frames[segment.start_frame_index : segment.end_frame_index])

    @staticmethod
    def _alive(
        session: DeviceSession,
        turn: ConversationTurn,
        generation: Generation | None = None,
        expected_queue_generation: int | None = None,
    ) -> bool:
        """Returns False once the turn is no longer safe to continue emitting."""
        if session.current_turn is not turn:
            return False
        if session.state in {SessionState.CLOSING, SessionState.CLOSED}:
            return False
        if turn.state in {TurnState.ABORTED, TurnState.FAILED, TurnState.COMPLETED}:
            return False
        if (
            expected_queue_generation is not None
            and session.egress_queue.generation != expected_queue_generation
        ):
            return False
        if generation is not None:
            try:
                session.ensure_current_generation(generation)
            except StaleGenerationError:
                return False
        return True
