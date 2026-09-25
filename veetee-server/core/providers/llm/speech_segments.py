from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass(frozen=True)
class SpeechSegmentationPolicy:
    """Configuration-owned policy for streaming LLM text into TTS units.

    These are transport/prosody thresholds, never semantic routing rules.
    Keeping them outside provider implementations makes latency tuning
    measurable and provider-independent.
    """

    min_segment_chars: int = 28
    clause_target_chars: int = 150
    clause_min_chars: int = 80
    first_clause_min_chars: int = 24
    first_clause_min_words: int = 4
    hard_max_segment_chars: int = 240
    hard_cut_search_back: int = 45
    hard_cut_search_forward: int = 40
    first_segment_min_chars: int = 8
    first_segment_min_words: int = 2
    # Optional latency-first soft cut for the first spoken unit. Zero keeps
    # legacy punctuation-only behavior. The cut is made only at whitespace,
    # never by semantic/keyword inspection.
    first_soft_cut_chars: int = 0
    first_soft_cut_min_words: int = 3

    @classmethod
    def from_config(cls, config: Any) -> "SpeechSegmentationPolicy":
        source = getattr(config, "speech_segmentation", config)
        return cls(
            min_segment_chars=int(getattr(source, "min_segment_chars", cls.min_segment_chars)),
            clause_target_chars=int(getattr(source, "clause_target_chars", cls.clause_target_chars)),
            clause_min_chars=int(getattr(source, "clause_min_chars", cls.clause_min_chars)),
            first_clause_min_chars=int(
                getattr(source, "first_clause_min_chars", cls.first_clause_min_chars)
            ),
            first_clause_min_words=int(
                getattr(source, "first_clause_min_words", cls.first_clause_min_words)
            ),
            hard_max_segment_chars=int(
                getattr(source, "hard_max_segment_chars", cls.hard_max_segment_chars)
            ),
            hard_cut_search_back=int(
                getattr(source, "hard_cut_search_back", cls.hard_cut_search_back)
            ),
            hard_cut_search_forward=int(
                getattr(source, "hard_cut_search_forward", cls.hard_cut_search_forward)
            ),
            first_segment_min_chars=int(
                getattr(source, "first_segment_min_chars", cls.first_segment_min_chars)
            ),
            first_segment_min_words=int(
                getattr(source, "first_segment_min_words", cls.first_segment_min_words)
            ),
            first_soft_cut_chars=int(
                getattr(source, "first_soft_cut_chars", cls.first_soft_cut_chars)
            ),
            first_soft_cut_min_words=int(
                getattr(source, "first_soft_cut_min_words", cls.first_soft_cut_min_words)
            ),
        )


class SpeechSegmentSplitter:
    """Split streamed text into natural, bounded TTS-sized speech segments."""

    def __init__(self, policy: Optional[SpeechSegmentationPolicy] = None):
        self.policy = policy or SpeechSegmentationPolicy()
        self.buffer = ""
        self.end_puncts = {".", "!", "?", "\n", "…", "。", "！", "？"}
        self.clause_puncts = {";", "；", ":", "：", ",", "，", "—"}
        self._segments_emitted = 0

    def _is_sentence_boundary(self, index: int) -> bool:
        char = self.buffer[index]
        if char != ".":
            return True
        if index + 1 >= len(self.buffer):
            return False
        prev_char = self.buffer[index - 1] if index > 0 else ""
        next_char = self.buffer[index + 1]
        if prev_char.isdigit() and next_char.isdigit():
            return False
        if next_char.isalpha() and not next_char.isspace():
            return False
        return True

    def _find_sentence_cut(self) -> int:
        visible_chars = 0
        for index, char in enumerate(self.buffer):
            if not char.isspace():
                visible_chars += 1
            if char not in self.end_puncts or not self._is_sentence_boundary(index):
                continue
            if (
                self._segments_emitted > 0
                and visible_chars >= self.policy.min_segment_chars
            ):
                return index
            if (
                self._segments_emitted == 0
                and visible_chars >= self.policy.first_segment_min_chars
            ):
                words = re.findall(
                    r"[^\W_]+", self.buffer[: index + 1], flags=re.UNICODE
                )
                if len(words) >= self.policy.first_segment_min_words:
                    return index
        return -1

    def _find_clause_cut(self) -> int:
        if self._segments_emitted == 0:
            visible_chars = 0
            for index, char in enumerate(self.buffer):
                if not char.isspace():
                    visible_chars += 1
                if (
                    char not in self.clause_puncts
                    or visible_chars < self.policy.first_clause_min_chars
                ):
                    continue
                words = re.findall(
                    r"[^\W_]+", self.buffer[: index + 1], flags=re.UNICODE
                )
                if len(words) >= self.policy.first_clause_min_words:
                    return index

        if len(self.buffer) < self.policy.clause_target_chars:
            return -1
        search_start = min(
            self.policy.clause_min_chars,
            self.policy.clause_target_chars,
        )
        search_end = min(len(self.buffer), self.policy.clause_target_chars + 1)
        for index in range(search_end - 1, search_start - 1, -1):
            if self.buffer[index] in self.clause_puncts:
                return index
        return -1

    def _find_first_soft_cut(self) -> int:
        target = int(self.policy.first_soft_cut_chars)
        if self._segments_emitted != 0 or target <= 0:
            return -1

        visible_chars = 0
        for index, char in enumerate(self.buffer):
            if not char.isspace():
                visible_chars += 1
                continue
            if visible_chars < target:
                continue
            words = re.findall(
                r"[^\W_]+", self.buffer[: index + 1], flags=re.UNICODE
            )
            if len(words) >= self.policy.first_soft_cut_min_words:
                return index
        return -1

    def _find_hard_cut(self) -> int:
        if len(self.buffer) < self.policy.hard_max_segment_chars:
            return -1

        search_start = max(
            0,
            self.policy.hard_max_segment_chars - self.policy.hard_cut_search_back,
        )
        search_end = min(
            len(self.buffer),
            self.policy.hard_max_segment_chars + 1,
        )
        for index in range(search_end - 1, search_start - 1, -1):
            if self.buffer[index] in self.clause_puncts:
                return index
        for index in range(search_end - 1, search_start - 1, -1):
            if self.buffer[index].isspace():
                return index

        forward_end = min(
            len(self.buffer),
            self.policy.hard_max_segment_chars
            + self.policy.hard_cut_search_forward,
        )
        for index in range(search_end, forward_end):
            if self.buffer[index].isspace():
                return index
        return -1

    def add_token(self, token: str) -> List[str]:
        self.buffer += token
        segments: List[str] = []
        while True:
            cut = self._find_sentence_cut()
            if cut == -1:
                cut = self._find_clause_cut()
            if cut == -1:
                cut = self._find_first_soft_cut()
            if cut == -1:
                cut = self._find_hard_cut()
            if cut == -1:
                break

            segment = self.buffer[: cut + 1].strip()
            self.buffer = self.buffer[cut + 1 :]
            if segment:
                segments.append(segment)
                self._segments_emitted += 1
        return segments

    def flush(self) -> List[str]:
        remainder = self.buffer.strip()
        self.buffer = ""
        if not remainder:
            return []
        self._segments_emitted += 1
        return [remainder]
