from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChunkReason = Literal["sentence", "emergency", "final", "length"]
ChunkingMode = Literal["length_bounded", "sentence_bounded"]


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    reason: ChunkReason


@dataclass(frozen=True, slots=True)
class TtsTextChunkingPolicy:
    mode: ChunkingMode = "length_bounded"
    emergency_max_characters: int = 160
    sentence_batch_max_characters: int | None = None

    def __post_init__(self) -> None:
        if self.emergency_max_characters < 1:
            raise ValueError("emergency_max_characters must be positive")
        if (
            self.sentence_batch_max_characters is not None
            and self.sentence_batch_max_characters < 1
        ):
            raise ValueError("sentence_batch_max_characters must be positive")
        if (
            self.sentence_batch_max_characters is not None
            and self.sentence_batch_max_characters > self.emergency_max_characters
        ):
            raise ValueError(
                "sentence_batch_max_characters cannot exceed emergency_max_characters"
            )


class SentenceChunker:
    _DEFAULT_SENTENCE_ABBREVIATIONS = (
        "gs.",
        "p.",
        "pgs.",
        "q.",
        "ths.",
        "tp.",
        "ts.",
        "v.d.",
        "v.v.",
    )
    _SENTENCE_TERMINATORS = (
        ".!?…。”\N{FULLWIDTH EXCLAMATION MARK}\N{FULLWIDTH QUESTION MARK}"
    )
    _TRAILING_CLOSERS = "\"')]}»”\N{RIGHT SINGLE QUOTATION MARK}"

    def __init__(
        self,
        min_characters: int,
        abbreviations: tuple[str, ...] = (),
        *,
        target_characters: int = 72,
        max_characters: int = 160,
        punctuation_min_characters: int | None = None,
        initial_target_characters: int | None = None,
        initial_max_characters: int | None = None,
        initial_punctuation_min_characters: int | None = None,
        mode: ChunkingMode = "length_bounded",
        emergency_max_characters: int | None = None,
        sentence_batch_max_characters: int | None = None,
    ) -> None:
        if min_characters < 1:
            raise ValueError("min_characters must be positive")
        if target_characters < min_characters:
            raise ValueError("target_characters must be at least min_characters")
        if max_characters < target_characters:
            raise ValueError("max_characters must be at least target_characters")
        resolved_punctuation_min = punctuation_min_characters or min_characters
        if not min_characters <= resolved_punctuation_min <= max_characters:
            raise ValueError(
                "punctuation_min_characters must be between min_characters and max_characters"
            )
        resolved_initial_target = initial_target_characters or target_characters
        resolved_initial_max = initial_max_characters or max_characters
        resolved_initial_punctuation_min = (
            initial_punctuation_min_characters or resolved_punctuation_min
        )
        if not min_characters <= resolved_initial_target <= target_characters:
            raise ValueError(
                "initial_target_characters must be between min_characters and target_characters"
            )
        if not resolved_initial_target <= resolved_initial_max <= max_characters:
            raise ValueError(
                "initial_max_characters must be between initial_target_characters "
                "and max_characters"
            )
        if not min_characters <= resolved_initial_punctuation_min <= resolved_initial_max:
            raise ValueError(
                "initial_punctuation_min_characters must be between min_characters "
                "and initial_max_characters"
            )
        resolved_emergency_max = emergency_max_characters or max_characters
        if resolved_emergency_max < min_characters:
            raise ValueError(
                "emergency_max_characters must be at least min_characters"
            )
        resolved_sentence_batch_max = (
            sentence_batch_max_characters or resolved_emergency_max
        )
        if not min_characters <= resolved_sentence_batch_max <= resolved_emergency_max:
            raise ValueError(
                "sentence_batch_max_characters must be between min_characters "
                "and emergency_max_characters"
            )
        self._min_characters = min_characters
        self._target_characters = target_characters
        self._max_characters = max_characters
        self._punctuation_min_characters = resolved_punctuation_min
        self._initial_target_characters = resolved_initial_target
        self._initial_max_characters = resolved_initial_max
        self._initial_punctuation_min_characters = (
            resolved_initial_punctuation_min
        )
        self._mode = mode
        self._emergency_max_characters = resolved_emergency_max
        self._sentence_batch_max_characters = resolved_sentence_batch_max
        configured_abbreviations = tuple(item.casefold() for item in abbreviations)
        self._abbreviations = (
            tuple(
                dict.fromkeys(
                    (*self._DEFAULT_SENTENCE_ABBREVIATIONS, *configured_abbreviations)
                )
            )
            if mode == "sentence_bounded"
            else configured_abbreviations
        )
        self._buffer = ""
        self._pending_sentences = ""
        self._emitted = False

    def push(self, text: str) -> list[str]:
        return [chunk.text for chunk in self.push_chunks(text)]

    def push_chunks(self, text: str) -> list[TextChunk]:
        self._buffer += text
        if self._mode == "sentence_bounded":
            return self._push_sentence_bounded()
        return self._push_length_bounded()

    def flush(self) -> str | None:
        chunks = self.flush_chunks()
        return " ".join(chunk.text for chunk in chunks) or None

    def flush_chunk(self) -> TextChunk | None:
        chunks = self.flush_chunks()
        if not chunks:
            return None
        if len(chunks) > 1:
            raise RuntimeError("flush_chunk cannot represent multiple pending TTS batches")
        return chunks[0]

    def flush_chunks(self) -> list[TextChunk]:
        remainder = self._buffer.strip()
        self._buffer = ""
        if self._mode != "sentence_bounded":
            return [TextChunk(remainder, "final")] if remainder else []
        if remainder and not self._has_spoken_content(remainder):
            if self._pending_sentences:
                self._pending_sentences = self._attach_punctuation(
                    self._pending_sentences, remainder
                )
            remainder = ""
        if not self._pending_sentences:
            return [TextChunk(remainder, "final")] if remainder else []
        combined = self._join_text(self._pending_sentences, remainder)
        if len(combined) <= self._sentence_batch_max_characters:
            self._pending_sentences = ""
            return [TextChunk(combined, "final")]
        chunks = [TextChunk(self._pending_sentences, "sentence")]
        self._pending_sentences = ""
        if remainder:
            chunks.append(TextChunk(remainder, "final"))
        return chunks

    def _push_sentence_bounded(self) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        while self._buffer:
            sentence_boundary = self._confirmed_sentence_boundary()
            if (
                sentence_boundary is not None
                and sentence_boundary <= self._emergency_max_characters
            ):
                sentence = self._buffer[:sentence_boundary].strip()
                self._buffer = self._buffer[sentence_boundary:].lstrip()
                if not self._has_spoken_content(sentence):
                    if self._pending_sentences:
                        self._pending_sentences = self._attach_punctuation(
                            self._pending_sentences, sentence
                        )
                    continue
                candidate = self._join_text(self._pending_sentences, sentence)
                if (
                    self._pending_sentences
                    and len(candidate) > self._sentence_batch_max_characters
                ):
                    self._append(
                        chunks,
                        self._pending_sentences,
                        "sentence",
                    )
                    self._pending_sentences = sentence
                else:
                    self._pending_sentences = candidate
                continue
            if len(self._buffer) <= self._emergency_max_characters:
                break
            if self._pending_sentences:
                self._append(chunks, self._pending_sentences, "sentence")
                self._pending_sentences = ""
                continue
            emergency_boundary = self._emergency_boundary()
            emergency = self._buffer[:emergency_boundary].strip()
            self._buffer = self._buffer[emergency_boundary:].lstrip()
            if self._has_spoken_content(emergency):
                self._append(chunks, emergency, "emergency")
        return chunks

    @staticmethod
    def _join_text(left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left
        return f"{left} {right}"

    @staticmethod
    def _has_spoken_content(text: str) -> bool:
        return any(character.isalnum() for character in text)

    @staticmethod
    def _attach_punctuation(text: str, punctuation: str) -> str:
        return f"{text.rstrip()}{punctuation.strip()}"

    def _confirmed_sentence_boundary(self) -> int | None:
        index = 0
        while index < len(self._buffer):
            character = self._buffer[index]
            if character in "\r\n":
                boundary = index + 1
                while boundary < len(self._buffer) and self._buffer[boundary] in "\r\n":
                    boundary += 1
                return boundary
            if character not in self._SENTENCE_TERMINATORS:
                index += 1
                continue
            if character == "." and self._period_is_non_terminal(index):
                index += 1
                continue
            boundary = index + 1
            while (
                boundary < len(self._buffer)
                and self._buffer[boundary] in self._SENTENCE_TERMINATORS
            ):
                boundary += 1
            while (
                boundary < len(self._buffer)
                and self._buffer[boundary] in self._TRAILING_CLOSERS
            ):
                boundary += 1
            # Keep a terminal cluster at the end of a delta until one character of
            # look-ahead arrives. This prevents `3.` + `14` and closing quotes from
            # becoming separate TTS requests.
            if boundary == len(self._buffer):
                return None
            return boundary
        return None

    def _period_is_non_terminal(self, index: int) -> bool:
        previous = self._buffer[index - 1] if index > 0 else ""
        following = self._buffer[index + 1] if index + 1 < len(self._buffer) else ""
        if previous.isdigit() and (not following or following.isdigit()):
            return True
        if previous.isalpha() and following.isalpha():
            return True
        candidate = self._buffer[: index + 1].strip().casefold()
        return self._is_abbreviation(candidate)

    def _emergency_boundary(self) -> int:
        limit = min(self._emergency_max_characters, len(self._buffer))
        for index in range(limit - 1, self._min_characters - 1, -1):
            if self._buffer[index].isspace():
                return index + 1
        return limit

    def _push_length_bounded(self) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        start = 0
        for index, character in enumerate(self._buffer):
            if character not in ".!?;:\n":
                continue
            candidate = self._buffer[start : index + 1].strip()
            if (
                len(candidate) < self._current_punctuation_min_characters
                or self._is_abbreviation(candidate)
            ):
                continue
            chunks.extend(self._bounded_sentence(candidate))
            start = index + 1
        self._buffer = self._buffer[start:].lstrip()
        while len(self._buffer) >= self._current_target_characters:
            soft_boundary = self._soft_boundary()
            if soft_boundary is None:
                break
            self._append(
                chunks,
                self._buffer[:soft_boundary].strip(),
                "length",
            )
            self._buffer = self._buffer[soft_boundary:].lstrip()
        while len(self._buffer) > self._current_max_characters:
            hard_boundary = self._length_boundary()
            if hard_boundary is None:
                break
            self._append(
                chunks,
                self._buffer[:hard_boundary].strip(),
                "length",
            )
            self._buffer = self._buffer[hard_boundary:].lstrip()
        return chunks

    def _is_abbreviation(self, candidate: str) -> bool:
        folded = candidate.casefold()
        return any(folded.endswith(item) for item in self._abbreviations)

    def _soft_boundary(self) -> int | None:
        """Prefer a natural clause pause when a stream has enough text."""
        boundary = -1
        for index, character in enumerate(self._buffer):
            if index + 1 > self._current_max_characters:
                break
            if character in ",\N{FULLWIDTH COMMA}、":
                if (
                    len(self._buffer[: index + 1].strip())
                    >= self._current_target_characters
                ):
                    boundary = index + 1
        return boundary if boundary >= 0 else None

    def _length_boundary(self) -> int | None:
        """Prevent a missing punctuation mark from blocking the speech stream."""
        limit = min(self._current_max_characters, len(self._buffer))
        for index in range(limit - 1, self._min_characters - 1, -1):
            if self._buffer[index].isspace():
                return index + 1
        # A pathological stream without whitespace must still release memory.
        return self._current_max_characters

    def _bounded_sentence(self, sentence: str) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        remainder = sentence
        while len(remainder) > self._current_max_characters:
            limit = min(self._current_target_characters, len(remainder))
            boundary = next(
                (
                    index + 1
                    for index in range(limit - 1, self._min_characters - 1, -1)
                    if remainder[index].isspace()
                ),
                limit,
            )
            self._append(chunks, remainder[:boundary].strip(), "length")
            remainder = remainder[boundary:].lstrip()
        if remainder:
            self._append(chunks, remainder, "sentence")
        return chunks

    @property
    def _current_target_characters(self) -> int:
        return (
            self._target_characters
            if self._emitted
            else self._initial_target_characters
        )

    @property
    def _current_max_characters(self) -> int:
        return self._max_characters if self._emitted else self._initial_max_characters

    @property
    def _current_punctuation_min_characters(self) -> int:
        return (
            self._punctuation_min_characters
            if self._emitted
            else self._initial_punctuation_min_characters
        )

    def _append(
        self,
        chunks: list[TextChunk],
        chunk: str,
        reason: ChunkReason,
    ) -> None:
        if not chunk:
            return
        chunks.append(TextChunk(chunk, reason))
        self._emitted = True
