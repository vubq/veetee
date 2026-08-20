"""Typed events emitted by the Veetee fake AI pipeline (M1.6).

Events are data-only: they describe *what happened* in the pipeline. A sink
(e.g. the device gateway) is responsible for translating them into wire
messages or downlink items. Keeping events free of transport concerns lets the
pipeline be tested with a simple recording sink and later be reused by other
providers without changing the orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SttEvent:
    """A transcription result for a captured utterance."""

    text: str
    session_id: str


@dataclass(frozen=True, slots=True)
class TtsStartEvent:
    """TTS response started; the session now holds a streaming generation."""

    session_id: str


@dataclass(frozen=True, slots=True)
class TtsSentenceStartEvent:
    """A new TTS sentence is about to be spoken."""

    text: str
    sentence_id: int
    session_id: str


@dataclass(frozen=True, slots=True)
class TtsChunkEvent:
    """A single synthesized PCM chunk together with its encoded wire frame."""

    pcm: bytes
    frame: bytes
    duration_ms: float
    session_id: str
    timestamp_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TtsStopEvent:
    """TTS response finished; the turn should be completed."""

    session_id: str


type PipelineEvent = SttEvent | TtsStartEvent | TtsSentenceStartEvent | TtsChunkEvent | TtsStopEvent


class EventSink(Protocol):
    """Async sink for pipeline events."""

    async def emit(self, event: PipelineEvent) -> None: ...
