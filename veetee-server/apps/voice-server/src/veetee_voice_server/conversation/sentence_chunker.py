from __future__ import annotations


class SentenceChunker:
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
        self._min_characters = min_characters
        self._target_characters = target_characters
        self._max_characters = max_characters
        self._punctuation_min_characters = resolved_punctuation_min
        self._initial_target_characters = resolved_initial_target
        self._initial_max_characters = resolved_initial_max
        self._initial_punctuation_min_characters = (
            resolved_initial_punctuation_min
        )
        self._abbreviations = tuple(item.casefold() for item in abbreviations)
        self._buffer = ""
        self._emitted = False

    def push(self, text: str) -> list[str]:
        self._buffer += text
        chunks: list[str] = []
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
            self._append(chunks, self._buffer[:soft_boundary].strip())
            self._buffer = self._buffer[soft_boundary:].lstrip()
        while len(self._buffer) > self._current_max_characters:
            hard_boundary = self._length_boundary()
            if hard_boundary is None:
                break
            self._append(chunks, self._buffer[:hard_boundary].strip())
            self._buffer = self._buffer[hard_boundary:].lstrip()
        return chunks

    def flush(self) -> str | None:
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None

    def _is_abbreviation(self, candidate: str) -> bool:
        folded = candidate.casefold()
        return any(folded.endswith(item) for item in self._abbreviations)

    def _soft_boundary(self) -> int | None:
        """Prefer a natural clause pause when a stream has enough text."""
        boundary = -1
        for index, character in enumerate(self._buffer):
            if index + 1 > self._current_max_characters:
                break
            if character in ",\uFF0C\u3001":
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

    def _bounded_sentence(self, sentence: str) -> list[str]:
        chunks: list[str] = []
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
            self._append(chunks, remainder[:boundary].strip())
            remainder = remainder[boundary:].lstrip()
        if remainder:
            self._append(chunks, remainder)
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

    def _append(self, chunks: list[str], chunk: str) -> None:
        if not chunk:
            return
        chunks.append(chunk)
        self._emitted = True
