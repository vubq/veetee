from __future__ import annotations


class SentenceChunker:
    def __init__(self, min_characters: int, abbreviations: tuple[str, ...] = ()) -> None:
        if min_characters < 1:
            raise ValueError("min_characters must be positive")
        self._min_characters = min_characters
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
        return chunks

    def flush(self) -> str | None:
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None

    def _is_abbreviation(self, candidate: str) -> bool:
        folded = candidate.casefold()
        return any(folded.endswith(item) for item in self._abbreviations)
