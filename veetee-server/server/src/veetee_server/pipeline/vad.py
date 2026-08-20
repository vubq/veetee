"""Deterministic fake Voice Activity Detection for the M1.6 pipeline harness.

The FakeVAD turns a batch of decoded PCM frames (16 kHz mono, 60 ms each) into
a single speech segment using a strict, fully deterministic state machine:

- ``idle``: waiting for ``start_frames`` consecutive speech frames.
- ``counting``: some speech frames seen but not yet enough to start.
- ``speaking``: utterance in progress; ends after ``end_silence_frames``
  consecutive silence frames or when ``max_utterance_frames`` is reached.
- ``ended``: utterance finalized; remaining frames are ignored.

A frame is considered speech when its root-mean-square amplitude is at least
``speech_threshold``. All thresholds are integers/numbers, so identical input
always yields identical output - no RNG, no wall-clock dependence.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import StrEnum

_SAMPLE_WIDTH = 2  # 16-bit little-endian mono PCM


class VadEventKind(StrEnum):
    """Classification of a single processed frame."""

    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    SILENCE = "silence"
    PROCESSING = "processing"


@dataclass(frozen=True, slots=True)
class VadEvent:
    """Outcome of processing one PCM frame."""

    kind: VadEventKind
    frame_index: int


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    """Half-open frame index range ``[start_frame_index, end_frame_index)``."""

    start_frame_index: int
    end_frame_index: int


def pcm_rms(pcm_data: bytes) -> float:
    """Computes the root-mean-square amplitude of 16-bit little-endian PCM.

    Returns 0.0 for an empty buffer so silence frames are trivially detected.
    """
    if not pcm_data:
        return 0.0
    sample_count = len(pcm_data) // _SAMPLE_WIDTH
    samples = struct.unpack(f"<{sample_count}h", pcm_data[: sample_count * _SAMPLE_WIDTH])
    sum_squares = 0.0
    for sample in samples:
        sum_squares += sample * sample
    return math.sqrt(sum_squares / sample_count)


class FakeVAD:
    """State-machine VAD over PCM frames with deterministic boundaries."""

    def __init__(
        self,
        *,
        speech_threshold: float = 32.0,
        start_frames: int = 2,
        end_silence_frames: int = 3,
        max_utterance_frames: int = 200,
    ) -> None:
        if speech_threshold < 0:
            raise ValueError("speech_threshold must be non-negative")
        if start_frames <= 0:
            raise ValueError("start_frames must be positive")
        if end_silence_frames <= 0:
            raise ValueError("end_silence_frames must be positive")
        if max_utterance_frames < start_frames:
            raise ValueError("max_utterance_frames must be at least start_frames")

        self.speech_threshold = speech_threshold
        self.start_frames = start_frames
        self.end_silence_frames = end_silence_frames
        self.max_utterance_frames = max_utterance_frames

        self.reset()

    def reset(self) -> None:
        """Resets the state machine for a fresh utterance batch."""
        self._frame_index = 0
        self._state = "idle"
        self._speech_start_index: int | None = None
        self._speech_end_index: int | None = None
        self._consecutive_speech = 0
        self._silence_run = 0

    def process_frame(self, pcm_data: bytes) -> VadEvent:
        """Feeds one decoded PCM frame into the state machine.

        Returns the classification for the frame; callers typically only act
        on ``SPEECH_START``/``SPEECH_END`` and use ``finish()`` afterwards.
        """
        frame_index = self._frame_index
        self._frame_index += 1

        if self._state == "ended":
            return VadEvent(kind=VadEventKind.SILENCE, frame_index=frame_index)

        is_speech = pcm_rms(pcm_data) >= self.speech_threshold

        if is_speech:
            self._silence_run = 0
            if self._state == "idle":
                self._consecutive_speech = 1
                if self.start_frames == 1:
                    self._state = "speaking"
                    self._speech_start_index = frame_index
                    return VadEvent(kind=VadEventKind.SPEECH_START, frame_index=frame_index)
                self._state = "counting"
                return VadEvent(kind=VadEventKind.PROCESSING, frame_index=frame_index)
            if self._state == "counting":
                self._consecutive_speech += 1
                if self._consecutive_speech >= self.start_frames:
                    self._state = "speaking"
                    self._speech_start_index = frame_index - self._consecutive_speech + 1
                    return VadEvent(kind=VadEventKind.SPEECH_START, frame_index=frame_index)
                return VadEvent(kind=VadEventKind.PROCESSING, frame_index=frame_index)
            # speaking
            if (
                self._speech_start_index is not None
                and frame_index - self._speech_start_index + 1 >= self.max_utterance_frames
            ):
                self._state = "ended"
                self._speech_end_index = frame_index + 1
                return VadEvent(kind=VadEventKind.SPEECH_END, frame_index=frame_index)
            return VadEvent(kind=VadEventKind.PROCESSING, frame_index=frame_index)

        # silence frame
        if self._state == "counting":
            # Not enough consecutive speech frames -> the candidate is discarded.
            self._state = "idle"
            self._consecutive_speech = 0
            return VadEvent(kind=VadEventKind.SILENCE, frame_index=frame_index)
        if self._state == "speaking":
            self._silence_run += 1
            if self._silence_run >= self.end_silence_frames:
                self._state = "ended"
                self._speech_end_index = frame_index - self._silence_run + 1
                return VadEvent(kind=VadEventKind.SPEECH_END, frame_index=frame_index)
            if (
                self._speech_start_index is not None
                and frame_index - self._speech_start_index >= self.max_utterance_frames
            ):
                # Utterance grew too long; force-close it at the max boundary.
                self._state = "ended"
                self._speech_end_index = frame_index
                return VadEvent(kind=VadEventKind.SPEECH_END, frame_index=frame_index)
            return VadEvent(kind=VadEventKind.PROCESSING, frame_index=frame_index)
        return VadEvent(kind=VadEventKind.SILENCE, frame_index=frame_index)

    def finish(self) -> SpeechSegment | None:
        """Finalizes the batch and returns the speech segment, if any.

        - ``speaking`` at end of stream: the utterance extends to the last
          processed frame (exclusive end = current frame index).
        - ``ended``: the finalized segment is returned.
        - otherwise: ``None`` (no usable speech).
        """
        if self._speech_start_index is None:
            return None
        if self._state == "speaking":
            return SpeechSegment(
                start_frame_index=self._speech_start_index,
                end_frame_index=self._frame_index,
            )
        if self._state == "ended" and self._speech_end_index is not None:
            return SpeechSegment(
                start_frame_index=self._speech_start_index,
                end_frame_index=self._speech_end_index,
            )
        return None
