"""Incremental Vietnamese-aware token segmentation for spoken TTS text."""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)", re.IGNORECASE)
_RAW_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MARKDOWN_PREFIX = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|>\s+|\d+[.)]\s+)")
_MARKDOWN_MARKER = re.compile(r"(?:```\w*|```|`|\*\*|\*|__|~|~~)")
_WHITESPACE = re.compile(r"\s+")
_ABBREVIATIONS = {
    "ts.",
    "ths.",
    "pgs.",
    "gs.",
    "tp.",
    "q.",
    "p.",
    "mr.",
    "mrs.",
    "dr.",
    "ms.",
    "prof.",
    "st.",
    "co.",
    "ltd.",
    "inc.",
    "v.v.",
    "vv.",
    "v.",
}
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}", "“": "”", "‘": "’"}
_CLOSERS = set(_OPEN_TO_CLOSE.values())


@dataclass(frozen=True, slots=True)
class TTSSegmenterConfig:
    first_min_chars: int = 24
    min_chars: int = 48
    max_chars: int = 220
    max_wait_seconds: float = 0.35

    def __post_init__(self) -> None:
        if self.first_min_chars <= 0 or self.min_chars <= 0:
            raise ValueError("segment minimum lengths must be positive")
        if self.max_chars < max(self.first_min_chars, self.min_chars):
            raise ValueError("max_chars must cover both minimum lengths")
        if self.max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")


def normalize_spoken_text(text: str) -> str:
    """Remove visual-only syntax while preserving useful Vietnamese wording."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _RAW_URL.sub(" liên kết ", text)
    text = _MARKDOWN_PREFIX.sub("", text)
    text = _MARKDOWN_MARKER.sub("", text)
    text = text.replace("&", " và ")
    text = "".join(
        character
        for character in text
        if not (
            unicodedata.category(character) in {"So", "Cs", "Sk"}
            or character in {"©", "®", "™"}
        )
    )
    return _WHITESPACE.sub(" ", text).strip(" \t\r\n-*#>")


class TTSTokenSegmenter:
    """Stateful segmenter that emits balanced, normalized spoken text."""

    def __init__(
        self,
        config: TTSSegmenterConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or TTSSegmenterConfig()
        self._clock = clock
        self._buffer = ""
        self._buffered_at: float | None = None
        self._emitted = 0

    def reset(self) -> None:
        self._buffer = ""
        self._buffered_at = None
        self._emitted = 0

    @property
    def has_pending(self) -> bool:
        return bool(self._buffer.strip())

    @property
    def seconds_until_due(self) -> float | None:
        if self._buffered_at is None or not self.has_pending:
            return None
        return max(0.0, self.config.max_wait_seconds - (self._clock() - self._buffered_at))

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        if self._buffered_at is None:
            self._buffered_at = self._clock()
        self._buffer += delta
        return self._drain(force=False)

    def flush_due(self) -> list[str]:
        if self.seconds_until_due != 0.0:
            return []
        return self._drain(force=True)

    def finish(self) -> list[str]:
        return self._drain(force=True, finish=True)

    def _drain(self, *, force: bool, finish: bool = False) -> list[str]:
        segments: list[str] = []
        while self.has_pending:
            minimum = self.config.first_min_chars if self._emitted == 0 else self.config.min_chars
            boundary = self._find_boundary(minimum)
            if boundary is None and len(self._buffer) >= self.config.max_chars:
                boundary = self._hard_boundary()
            if boundary is None and (force or finish):
                boundary = len(self._buffer)
            if boundary is None:
                break
            raw = self._buffer[:boundary]
            self._buffer = self._buffer[boundary:].lstrip()
            spoken = normalize_spoken_text(raw)
            if spoken:
                segments.append(spoken)
                self._emitted += 1
            self._buffered_at = self._clock() if self.has_pending else None
        return segments

    def _find_boundary(self, minimum: int) -> int | None:
        stack: list[str] = []
        quote_open = False
        for index, character in enumerate(self._buffer):
            length = index + 1
            if length > self.config.max_chars:
                break
            if character in _OPEN_TO_CLOSE:
                stack.append(_OPEN_TO_CLOSE[character])
            elif character in _CLOSERS and stack and stack[-1] == character:
                stack.pop()
            elif character == '"':
                quote_open = not quote_open
            if length < minimum or stack or quote_open:
                continue
            if character in ".!?;:\n" and self._safe_terminal(index):
                return length
            if not stack and not quote_open and character in _CLOSERS | {'"'}:
                spoken = self._buffer[:index].rstrip().rstrip(")]}'\"”’")
                if spoken.endswith((".", "!", "?", ";", ":")):
                    return length
            if self._emitted == 0 and character == ",":
                previous = self._buffer[index - 1] if index > 0 else ""
                following = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
                if not (previous.isdigit() and following.isdigit()):
                    if not following or following.isspace() or following in _CLOSERS | {'"'}:
                        return length
        return None

    def _safe_terminal(self, index: int) -> bool:
        character = self._buffer[index]
        if character == ".":
            previous = self._buffer[index - 1] if index > 0 else ""
            following = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
            if previous == "." or following == ".":
                return False
            if previous.isdigit() and (following.isdigit() or not following):
                return False
            token = (
                self._buffer[: index + 1]
                .rsplit(maxsplit=1)[-1]
                .lstrip("([{“‘\"'")
                .lower()
            )
            if token in _ABBREVIATIONS or token.startswith(("http://", "https://", "www.")):
                return False
        following = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
        remainder = self._buffer[index + 1 :].lstrip()
        if remainder.startswith(tuple(_OPEN_TO_CLOSE) + ('"',)):
            return False
        return not following or following.isspace() or following in _CLOSERS | {'"'}

    def _hard_boundary(self) -> int:
        window = self._buffer[: self.config.max_chars]
        for separator in (" ", ",", ";"):
            position = window.rfind(separator)
            if position >= max(1, self.config.max_chars // 2):
                return position + 1
        return self.config.max_chars
