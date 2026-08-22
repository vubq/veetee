"""Fake AI pipeline orchestrator for the M1.6 and M2.4 milestones.

The orchestrator drives the real-time VAD -> ASR -> LLM -> Segmenter -> TTS chain for
one capture turn:

1. drain the session's ingress queue (all frames captured during LISTENING);
2. run the VAD over the decoded frames to extract a single speech segment;
3. transcribe (ASR);
4. stream tokens from LLM into a TTSTokenSegmenter in real time;
5. start TTS and emit audio chunks as soon as the first segment is ready;
6. emit typed events through an injected ``EventSink``.

The pipeline is turn-scoped: it is started from a processing turn's
cancellation scope, so an abort or a new (barge-in) turn cancels it at the
next await point. Every ``emit``/``await`` is preceded by an ``_alive`` check,
and stale-generation protection is enforced once streaming begins.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from veetee_server.audio.codec import AudioDecoder, AudioEncoder
from veetee_server.domain.errors import InvalidTransitionError, StaleGenerationError
from veetee_server.domain.session import (
    ConversationTurn,
    DeviceSession,
    Generation,
    SessionState,
    TurnState,
)
from veetee_server.prompt import ContextAssembler

from .asr import ASRProvider
from .events import (
    EventSink,
    SttEvent,
    TtsChunkEvent,
    TtsSentenceStartEvent,
    TtsStartEvent,
    TtsStopEvent,
)
from .framing import build_downlink_frame
from .llm import (
    LLMCompletedEvent,
    LLMProvider,
    LLMStreamEvent,
    LLMTextDeltaEvent,
    LLMUsageEvent,
)
from .segmenter import TTSTokenSegmenter
from .tts import FakeTTS
from .vad import BaseVADStream, FakeVAD

if TYPE_CHECKING:
    from veetee_server.dialogue.recorder import SessionTranscriptRecorder
    from veetee_server.persistence import QuotaService

logger = logging.getLogger("veetee.pipeline")

CorrectionHook = Callable[[str], Awaitable[tuple[str, dict[str, Any]]]]
ContextHook = Callable[[str], Awaitable[list[dict[str, Any]]]]

_DOWNLINK_FRAME_DURATION_MS = 60.0
_STREAM_END = object()


class PipelineOutcome(StrEnum):
    """Terminal outcomes of a pipeline run."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_UTTERANCE = "no_utterance"
    INVALID_START = "invalid_start"
    QUOTA_EXCEEDED = "quota_exceeded"


class FakePipeline:
    """Turn-scoped orchestrator over AI components."""

    def __init__(
        self,
        *,
        decoder: AudioDecoder,
        encoder: AudioEncoder,
        protocol_version: int,
        vad: FakeVAD | BaseVADStream | Any,
        asr: ASRProvider,
        llm: LLMProvider | Any,
        tts: FakeTTS | Any,
        segmenter: TTSTokenSegmenter | None = None,
        context_assembler: ContextAssembler | None = None,
        correction_hook: CorrectionHook | None = None,
        context_hook: ContextHook | None = None,
        quota_service: QuotaService | None = None,
        transcript_recorder: SessionTranscriptRecorder | None = None,
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
        self.segmenter = segmenter or TTSTokenSegmenter()
        self.context_assembler = context_assembler or ContextAssembler()
        self.correction_hook = correction_hook
        self.context_hook = context_hook
        self.quota_service = quota_service
        self.transcript_recorder = transcript_recorder
        self._now_ms = now_ms

    async def run(self, session: DeviceSession, sink: EventSink) -> PipelineOutcome:
        """Runs the pipeline for the session's current processing turn.

        Returns a ``PipelineOutcome`` string. May raise
        ``SlowClientQueueOverflowError`` when the downlink queue overflows
        under the fail-session policy; the gateway supervisor handles that as
        a slow-client close.
        """
        try:
            return await self._run(session, sink)
        finally:
            for codec in (self.decoder, self.encoder):
                close = getattr(codec, "close", None)
                if close is not None:
                    close()

    async def _run(self, session: DeviceSession, sink: EventSink) -> PipelineOutcome:
        """Runs one turn while ``run`` owns codec cleanup."""
        turn = session.current_turn
        if turn is None or turn.state is not TurnState.PROCESSING:
            return PipelineOutcome.INVALID_START

        expected_queue_gen = session.egress_queue.generation

        utterance = await self._collect_utterance(session, turn)
        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED
        if utterance is None:
            return PipelineOutcome.NO_UTTERANCE

        asr_result = await self.asr.transcribe_async(utterance)
        transcript = asr_result.normalized_text or asr_result.raw_text

        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED
        if not transcript or not transcript.strip():
            return PipelineOutcome.NO_UTTERANCE

        correction_provenance: dict[str, Any] = {}
        if self.correction_hook is not None:
            transcript, correction_provenance = await self.correction_hook(transcript)

        await sink.emit(SttEvent(text=transcript, session_id=str(session.id)))
        if not self._alive(session, turn, expected_queue_generation=expected_queue_gen):
            return PipelineOutcome.CANCELLED

        if self.transcript_recorder is not None:
            await asyncio.to_thread(
                self.transcript_recorder.record_user_turn,
                raw_transcript=asr_result.raw_text,
                normalized_text=asr_result.normalized_text,
                model_text=transcript,
                metadata={"correction": correction_provenance}
                if correction_provenance
                else None,
            )

        # Platform and conversation policies are baseline for every turn.
        # A bound snapshot only adds its profile inside that hierarchy.
        provider_contexts = (
            await self.context_hook(transcript) if self.context_hook is not None else []
        )
        assembled = self.context_assembler.assemble(
            agent_profile=(
                turn.snapshot.prompt_profile if turn.snapshot is not None else None
            ),
            user_turn=transcript,
            provider_contexts=provider_contexts,
        )
        if correction_provenance:
            assembled.metadata["correction"] = correction_provenance
        messages = assembled.messages
        self.segmenter.reset()

        owner_user_id = session.owner_user_id
        if self.quota_service is not None and owner_user_id is not None:
            quota = await asyncio.to_thread(
                self.quota_service.check_only, owner_user_id, "llm_tokens_day"
            )
            if not quota.allowed:
                return PipelineOutcome.QUOTA_EXCEEDED

        segment_queue: asyncio.Queue[str | Exception | object] = asyncio.Queue(maxsize=2)
        assistant_text_parts: list[str] = []
        assistant_final_text = ""

        async def producer() -> None:
            nonlocal assistant_final_text
            stream = self.llm.generate_stream(messages)
            next_event: asyncio.Task[LLMStreamEvent] | None = None
            cancelled = False

            try:
                while True:
                    if not self._alive(
                        session, turn, expected_queue_generation=expected_queue_gen
                    ):
                        break

                    timeout = self.segmenter.seconds_until_due
                    if timeout is not None and timeout <= 0.0:
                        for seg in self.segmenter.flush_due():
                            await segment_queue.put(seg)
                        timeout = self.segmenter.seconds_until_due

                    if next_event is None:
                        next_event = asyncio.create_task(anext(stream))
                    done, _ = await asyncio.wait(
                        {next_event},
                        timeout=timeout,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        for seg in self.segmenter.flush_due():
                            await segment_queue.put(seg)
                        continue

                    try:
                        item = next_event.result()
                    except StopAsyncIteration:
                        for seg in self.segmenter.finish():
                            await segment_queue.put(seg)
                        break
                    finally:
                        next_event = None

                    if isinstance(item, LLMTextDeltaEvent) and not item.reasoning:
                        assistant_text_parts.append(item.delta)
                        for seg in self.segmenter.feed(item.delta):
                            await segment_queue.put(seg)
                    elif isinstance(item, LLMCompletedEvent):
                        assistant_final_text = item.text
                    elif (
                        isinstance(item, LLMUsageEvent)
                        and self.quota_service is not None
                        and owner_user_id is not None
                        and item.usage.total_tokens > 0
                    ):
                        await asyncio.to_thread(
                            self.quota_service.record_usage,
                            owner_user_id,
                            "llm_tokens_day",
                            item.usage.total_tokens,
                        )
            except Exception as exc:
                logger.warning("LLM stream failed", extra={"error_type": type(exc).__name__})
                await segment_queue.put(exc)
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                if next_event is not None and not next_event.done():
                    next_event.cancel()
                    try:
                        await next_event
                    except BaseException:
                        pass
                await stream.aclose()
                if not cancelled:
                    await segment_queue.put(_STREAM_END)

        producer_task = asyncio.create_task(producer())

        try:
            first_segment = True
            sentence_id = 0
            generation: Generation | None = None

            while True:
                segment = await segment_queue.get()
                if segment is _STREAM_END:
                    break
                if isinstance(segment, Exception):
                    raise segment
                if not isinstance(segment, str):
                    raise TypeError("unexpected segment queue item")

                if self.quota_service is not None and owner_user_id is not None:
                    quota = await asyncio.to_thread(
                        self.quota_service.check_and_consume,
                        owner_user_id,
                        "tts_chars_day",
                        len(segment),
                    )
                    if not quota.allowed:
                        producer_task.cancel()
                        return PipelineOutcome.QUOTA_EXCEEDED

                if first_segment:
                    first_segment = False
                    try:
                        generation = session.begin_streaming()
                    except InvalidTransitionError:
                        producer_task.cancel()
                        return PipelineOutcome.CANCELLED

                    if not self._alive(
                        session,
                        turn,
                        generation,
                        expected_queue_generation=expected_queue_gen,
                    ):
                        producer_task.cancel()
                        return PipelineOutcome.CANCELLED

                    await sink.emit(TtsStartEvent(session_id=str(session.id)))

                if not self._alive(
                    session,
                    turn,
                    generation,
                    expected_queue_generation=expected_queue_gen,
                ):
                    producer_task.cancel()
                    return PipelineOutcome.CANCELLED

                sentence_id += 1
                await sink.emit(
                    TtsSentenceStartEvent(
                        text=segment,
                        sentence_id=sentence_id,
                        session_id=str(session.id),
                    )
                )

                async for pcm in self.tts.synthesize(segment):
                    if not self._alive(
                        session,
                        turn,
                        generation,
                        expected_queue_generation=expected_queue_gen,
                    ):
                        producer_task.cancel()
                        return PipelineOutcome.CANCELLED
                    opus = self.encoder.encode(pcm)
                    frame = build_downlink_frame(
                        self.protocol_version, opus, now_ms=self._now_ms
                    )
                    await sink.emit(
                        TtsChunkEvent(
                            pcm=pcm,
                            frame=frame,
                            duration_ms=_DOWNLINK_FRAME_DURATION_MS,
                            session_id=str(session.id),
                        )
                    )

            if first_segment:
                return PipelineOutcome.NO_UTTERANCE

            if not self._alive(
                session,
                turn,
                generation,
                expected_queue_generation=expected_queue_gen,
            ):
                return PipelineOutcome.CANCELLED

            await sink.emit(TtsStopEvent(session_id=str(session.id)))
            try:
                session.complete_turn()
            except InvalidTransitionError:
                return PipelineOutcome.CANCELLED
            if self.transcript_recorder is not None:
                await asyncio.to_thread(
                    self.transcript_recorder.record_assistant_turn,
                    assistant_final_text or "".join(assistant_text_parts),
                )
            return PipelineOutcome.COMPLETED
        finally:
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except BaseException:
                    pass

    async def _collect_utterance(
        self, session: DeviceSession, turn: ConversationTurn
    ) -> bytes | None:
        """Drains captured frames and returns the VAD speech segment PCM.

        After ``listen/stop`` the gateway drops any further audio, so the whole
        utterance is already buffered. Returning ``None`` means no usable speech was found.
        """
        items = session.ingress_queue.drain()
        if not items:
            return None
        self.vad.reset()
        frames: list[bytes] = []
        for item in items:
            pcm = self.decoder.decode(item.payload)
            frames.append(pcm)
            if hasattr(self.vad, "process_pcm_async"):
                await self.vad.process_pcm_async(pcm)
            else:
                self.vad.process_frame(pcm)

        segment = self.vad.finish()
        if segment is None:
            return None

        if hasattr(segment, "end_frame_index") and hasattr(segment, "start_frame_index"):
            if segment.end_frame_index <= segment.start_frame_index:
                return None
            return b"".join(frames[segment.start_frame_index : segment.end_frame_index])

        if hasattr(segment, "pcm_data"):
            return segment.pcm_data if segment.pcm_data else None

        return None

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
