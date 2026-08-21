"""Deterministic fake Speech-to-Text for the M1.6 pipeline harness.

Transcription is purely deterministic: an optional fingerprint map lets tests
pin a transcript to an exact PCM buffer (keyed by SHA-256); any other buffer
falls back to ``default_text``. There is no network, no model, and no timing
dependency, so pipeline tests are repeatable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .contract import ASRResult, normalize_transcript

_DEFAULT_TRANSCRIPT = "Xin chào, đây là phản hồi mẫu từ Veetee."


class FakeASR:
    """Fake transcription provider with fingerprint mapping support."""

    def __init__(
        self,
        default_text: str = _DEFAULT_TRANSCRIPT,
        fingerprints: Mapping[str, str] | None = None,
    ) -> None:
        if not default_text or not default_text.strip():
            raise ValueError("default_text must be a non-empty string")
        self.default_text = default_text
        self.fingerprints: Mapping[str, str] = dict(fingerprints or {})

    def transcribe(self, pcm_data: bytes) -> str:
        """Returns a deterministic transcript for a PCM utterance."""
        fingerprint = hashlib.sha256(pcm_data).hexdigest()
        return self.fingerprints.get(fingerprint, self.default_text)

    async def transcribe_async(self, request: bytes | Any) -> ASRResult:
        """Async transcription returning typed ASRResult."""
        pcm_bytes = request.pcm_data if hasattr(request, "pcm_data") else request
        raw_text = self.transcribe(pcm_bytes)
        norm_text = normalize_transcript(raw_text)
        duration = float(len(pcm_bytes) / 32000.0) if isinstance(pcm_bytes, bytes) else 0.0
        return ASRResult(
            raw_text=raw_text,
            normalized_text=norm_text,
            language="vi",
            duration_seconds=duration,
            segments=[],
            provider_metadata={"fake": True},
        )
