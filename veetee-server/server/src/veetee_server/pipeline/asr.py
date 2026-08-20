"""Deterministic fake Speech-to-Text for the M1.6 pipeline harness.

Transcription is purely deterministic: an optional fingerprint map lets tests
pin a transcript to an exact PCM buffer (keyed by SHA-256); any other buffer
falls back to ``default_text``. There is no network, no model, and no timing
dependency, so pipeline tests are repeatable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

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
