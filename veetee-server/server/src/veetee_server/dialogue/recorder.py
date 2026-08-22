"""Typed conversation recording service bridging dialogue turns to PostgreSQL.

The recorder is the single write hook between the realtime pipeline and
conversation persistence. Recording is opt-in: without a versioned transcript
consent nothing is persisted, and callers receive ``None`` instead of rows.
Raw audio is never accepted or stored; only pipeline-produced text fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from veetee_server.dialogue.history import DialogueTurn
from veetee_server.persistence.conversation import ConversationRepository, StoredTurn, TurnInput


@dataclass(frozen=True, slots=True)
class RecordingContext:
    """Per-conversation handle produced by :meth:`ConversationRecorder.begin`."""

    owner_user_id: uuid.UUID
    conversation_id: uuid.UUID | None
    transcript_consent: bool
    consent_version: str


class ConversationRecorder:
    """Persists conversation shells and turns when (and only when) consented."""

    def __init__(self, repository: ConversationRepository, retention_days: int = 30) -> None:
        self._repository = repository
        self._retention_days = retention_days

    def begin(
        self,
        owner_user_id: uuid.UUID,
        *,
        agent_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        locale: str = "vi-VN",
        title: str = "",
        transcript_consent: bool,
        consent_version: str = "",
    ) -> RecordingContext:
        if not transcript_consent:
            # Opt-out: no shell, no turns — nothing about this conversation is stored.
            return RecordingContext(
                owner_user_id=owner_user_id,
                conversation_id=None,
                transcript_consent=False,
                consent_version="",
            )
        conversation = self._repository.get_or_create(
            owner_user_id,
            agent_id=agent_id,
            device_id=device_id,
            title=title,
            locale=locale,
            retention_days=self._retention_days,
        )
        consented = self._repository.update(
            owner_user_id,
            conversation.id,
            transcript_consent=True,
            consent_version=consent_version,
        )
        if consented is None:  # pragma: no cover - row exists in the same operation
            raise RuntimeError("Conversation vanished while applying transcript consent")
        return RecordingContext(
            owner_user_id=owner_user_id,
            conversation_id=consented.id,
            transcript_consent=True,
            consent_version=consent_version,
        )

    def record_turn(self, context: RecordingContext, turn: DialogueTurn) -> StoredTurn | None:
        """Maps one in-memory :class:`DialogueTurn` into a persisted turn row."""
        if not context.transcript_consent or context.conversation_id is None:
            return None

        raw = normalized = model_text = ""
        tool_calls: list[dict[str, Any]] = []
        tool_call_id = ""
        tool_name = ""
        provenance: dict[str, Any] = dict(turn.metadata)

        if turn.role == "user" and turn.transcript_pair is not None:
            raw = turn.transcript_pair.raw_transcript
            normalized = turn.transcript_pair.normalized_text
            model_text = turn.transcript_pair.text_for_model
        elif turn.role == "assistant":
            content = turn.content
            model_text = content
            if turn.tool_calls:
                tool_calls = list(turn.tool_calls)
        elif turn.role == "tool":
            content = turn.content
            tool_call_id = turn.tool_call_id or ""
            tool_name = turn.name or ""

        return self._repository.append_turn(
            context.owner_user_id,
            context.conversation_id,
            TurnInput(
                turn_id=turn.turn_id,
                role=turn.role,
                content=turn.content,
                raw_transcript=raw,
                normalized_text=normalized,
                model_text=model_text,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                provenance=provenance,
            ),
        )
