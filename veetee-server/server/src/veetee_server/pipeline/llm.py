"""Deterministic fake LLM for the M1.6 pipeline harness.

The fake LLM splits the transcript into sentences on ``.``/``!``/``?``
boundaries. This gives the TTS stage a well-defined, deterministic unit of
work and exercises the ``sentence_start`` wire message end to end without
introducing any external dependency or randomness.
"""

from __future__ import annotations

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class FakeLLM:
    """Sentence-level segmentation over a transcript."""

    def segments(self, transcript: str) -> list[str]:
        """Splits a transcript into non-empty sentences.

        Transcripts without punctuation boundaries yield a single segment so
        the pipeline still produces a response.
        """
        parts = [part.strip() for part in _SENTENCE_SPLIT.split(transcript) if part.strip()]
        return parts or [transcript]
