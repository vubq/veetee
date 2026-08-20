"""Deterministic fake Text-to-Speech for the M1.6 pipeline harness.

Every sentence is expanded into ``chunks_per_sentence`` PCM chunks whose bytes
are derived from the SHA-256 of the sentence text, so the same sentence always
produces the exact same downlink stream. An optional ``delay_seconds`` lets
tests open a deterministic window for cancellation/abort scenarios.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator

from veetee_server.audio.codec import DOWNLINK_PCM_FORMAT, PCMFormat


class FakeTTS:
    """Deterministic PCM chunk synthesizer."""

    def __init__(
        self,
        chunks_per_sentence: int = 3,
        pcm_format: PCMFormat = DOWNLINK_PCM_FORMAT,
        delay_seconds: float = 0.0,
    ) -> None:
        if chunks_per_sentence <= 0:
            raise ValueError("chunks_per_sentence must be positive")
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        self.chunks_per_sentence = chunks_per_sentence
        self.pcm_format = pcm_format
        self.delay_seconds = delay_seconds

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        """Yields deterministic PCM chunks for a sentence.

        The iterator is cancellable: awaiting the optional delay is an
        asyncio sleep point, so aborting the enclosing turn cancels the
        stream cleanly.
        """
        seed = hashlib.sha256(sentence.encode("utf-8")).digest()
        for index in range(self.chunks_per_sentence):
            if self.delay_seconds > 0:
                await asyncio.sleep(self.delay_seconds)
            yield self._chunk_pcm(seed, index)

    def _chunk_pcm(self, seed: bytes, index: int) -> bytes:
        expected_bytes = self.pcm_format.expected_bytes
        base = (seed[0] + index + 1) % 256 or 1  # ensure a non-zero pattern
        pattern = bytes([base]) * (expected_bytes // 1 + 1)
        return pattern[:expected_bytes]
