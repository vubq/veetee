"""Deterministic fake LLM for the M1.6 and M2.3 pipeline harness.

The fake LLM splits the transcript into sentences on ``.``/``!``/``?``
boundaries. This gives the TTS stage a well-defined, deterministic unit of
work and exercises the ``sentence_start`` wire message end to end without
introducing any external dependency or randomness.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from .contract import (
    ChatMessage,
    CompletedToolCall,
    LLMCompletedEvent,
    LLMStartedEvent,
    LLMStreamEvent,
    LLMTextDeltaEvent,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class FakeLLM:
    """Sentence-level segmentation and fake streaming LLM provider."""

    def segments(self, transcript: str) -> list[str]:
        """Splits a transcript into non-empty sentences.

        Transcripts without punctuation boundaries yield a single segment so
        the pipeline still produces a response.
        """
        parts = [part.strip() for part in _SENTENCE_SPLIT.split(transcript) if part.strip()]
        return parts or [transcript]

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[LLMStreamEvent]:
        """Yields deterministic streaming events for test pipeline compatibility."""
        user_text = ""
        for msg in reversed(messages):
            if msg.role == "user" and isinstance(msg.content, str):
                user_text = msg.content
                break

        yield LLMStartedEvent(
            provider="fake",
            model="fake-llm",
            request_id="fake-req-1",
            timestamp_ms=time.time() * 1000.0,
        )

        if user_text:
            yield LLMTextDeltaEvent(delta=user_text, index=0)

        tool_calls: list[CompletedToolCall] = []
        yield LLMCompletedEvent(
            text=user_text,
            tool_calls=tool_calls,
            finish_reason="stop",
            usage=None,
        )
