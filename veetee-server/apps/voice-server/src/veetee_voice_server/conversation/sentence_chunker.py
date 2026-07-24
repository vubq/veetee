from __future__ import annotations


class SentenceChunker:
    def __init__(
        self,
        min_characters: int,
        abbreviations: tuple[str, ...] = (),
        *,
        target_characters: int = 72,
        max_characters: int = 160,
    ) -> None:
        if min_characters < 1:
            raise ValueError("min_characters must be positive")
        if target_characters < min_characters:
            raise ValueError("target_characters must be at least min_characters")
        if max_characters < target_characters:
            raise ValueError("max_characters must be at least target_characters")
        self._min_characters = min_characters
        self._target_characters = target_characters
        self._max_characters = max_characters
        self._abbreviations = tuple(item.casefold() for item in abbreviations)
        self._buffer = ""

    def push(self, text: str) -> list[str]:
        self._buffer += text
        chunks: list[str] = []
        start = 0
        for index, character in enumerate(self._buffer):
            if character not in ".!?;:\n":
                continue
            candidate = self._buffer[start : index + 1].strip()
            if len(candidate) < self._min_characters or self._is_abbreviation(candidate):
                continue
            chunks.append(candidate)
            start = index + 1
        self._buffer = self._buffer[start:].lstrip()
        while len(self._buffer) >= self._target_characters:
            soft_boundary = self._soft_boundary()
            if soft_boundary is None:
                break
            chunks.append(self._buffer[:soft_boundary].strip())
            self._buffer = self._buffer[soft_boundary:].lstrip()
        while len(self._buffer) > self._max_characters:
            hard_boundary = self._length_boundary()
            if hard_boundary is None:
                break
            chunks.append(self._buffer[:hard_boundary].strip())
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
            if index + 1 > self._max_characters:
                break
            if character in ",\uFF0C\u3001":
                if len(self._buffer[: index + 1].strip()) >= self._target_characters:
                    boundary = index + 1
        return boundary if boundary >= 0 else None

    def _length_boundary(self) -> int | None:
        """Prevent a missing punctuation mark from blocking the speech stream."""
        limit = min(self._max_characters, len(self._buffer))
        for index in range(limit - 1, self._min_characters - 1, -1):
            if self._buffer[index].isspace():
                return index + 1
        # A pathological stream without whitespace must still release memory.
        return self._max_characters
