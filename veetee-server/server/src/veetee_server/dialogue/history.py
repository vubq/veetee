"""Session dialogue history representation with explicit transcript separation."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from veetee_server.pipeline.llm.contract import ChatMessage


@dataclass(frozen=True, slots=True)
class TranscriptPair:
    """Explicit separation of raw transcript, normalized text, and model text."""

    raw_transcript: str
    normalized_text: str
    text_for_model: str


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    """A single turn in session working history."""

    turn_id: str
    role: str  # "user", "assistant", "system", "tool"
    content: str
    transcript_pair: TranscriptPair | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_chat_message(self) -> ChatMessage:
        """Converts turn to OpenAI-compatible ChatMessage."""
        return ChatMessage(
            role=self.role,
            content=self.content,
            name=self.name,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
        )


class DialogueHistory:
    """Session-level working dialogue history."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self._turns: list[DialogueTurn] = []

    def add_user_turn(
        self,
        raw_transcript: str,
        normalized_text: str | None = None,
        text_for_model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DialogueTurn:
        """Adds a user turn with separated transcript representations."""
        raw = raw_transcript.strip()
        norm = (normalized_text or raw).strip()
        model_text = (text_for_model or norm).strip()

        pair = TranscriptPair(
            raw_transcript=raw,
            normalized_text=norm,
            text_for_model=model_text,
        )

        turn = DialogueTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            role="user",
            content=model_text,
            transcript_pair=pair,
            metadata=metadata or {},
        )
        self._turns.append(turn)
        return turn

    def add_assistant_turn(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DialogueTurn:
        """Adds an assistant response or tool call request turn."""
        turn = DialogueTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            metadata=metadata or {},
        )
        self._turns.append(turn)
        return turn

    def add_tool_result(
        self,
        tool_call_id: str,
        name: str,
        output: str,
        metadata: dict[str, Any] | None = None,
    ) -> DialogueTurn:
        """Adds a tool execution result turn."""
        turn = DialogueTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            role="tool",
            content=output,
            tool_call_id=tool_call_id,
            name=name,
            metadata=metadata or {},
        )
        self._turns.append(turn)
        return turn

    def get_turns(self) -> list[DialogueTurn]:
        """Returns a copy of all turns in order."""
        return list(self._turns)

    def set_turns(self, turns: list[DialogueTurn]) -> None:
        """Replaces turns (used by budget compaction)."""
        self._turns = list(turns)

    def to_chat_messages(self) -> list[ChatMessage]:
        """Converts all turns into ChatMessage list."""
        return [t.to_chat_message() for t in self._turns]

    def clear(self) -> None:
        """Clears all dialogue turns."""
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)
