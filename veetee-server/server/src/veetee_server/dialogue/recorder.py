"""Typed conversation recording service bridging dialogue turns to PostgreSQL.

The recorder is the single write hook between the realtime pipeline and
conversation persistence. Recording is opt-in: without a versioned transcript
consent nothing is persisted, and callers receive ``None`` instead of rows.
Raw audio is never accepted or stored; only pipeline-produced text fields.

Audit and log output produced around recording carries identifiers and error
types only, never transcript content.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from veetee_server.dialogue.history import DialogueTurn, TranscriptPair
from veetee_server.persistence.conversation import (
    ConversationRepository,
    StoredTurn,
    TurnInput,
)

logger = logging.getLogger("veetee.dialogue.recorder")


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
        consent_version = consent_version.strip()
        if not transcript_consent or not consent_version:
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


class SessionTranscriptRecorder:
    """Per-session transcript writer for one consented realtime session.

    Built only when the device consent snapshot taken at binding resolution is
    enabled, so every instance starts with an explicit stored grant. The
    conversation shell is created lazily on the first recorded turn and reused
    for the whole session. Every method performs synchronous database work and
    MUST be invoked through ``asyncio.to_thread`` by the pipeline so the event
    loop never blocks.

    Failure policy is fail-open for realtime: persistence errors disable the
    recorder for the rest of the session instead of disturbing the audio path.
    Logs carry identifiers and error types only; transcript text never reaches
    a log line or an exception message from this class.
    """

    def __init__(
        self,
        recorder: ConversationRecorder,
        *,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        device_id: uuid.UUID | None,
        consent_version: str,
        locale: str = "vi-VN",
        session_id: str = "",
    ) -> None:
        self._recorder = recorder
        self._owner_user_id = owner_user_id
        self._agent_id = agent_id
        self._device_id = device_id
        self._consent_version = consent_version
        self._locale = locale
        self._session_id = session_id
        self._context: RecordingContext | None = None
        self._disabled = False

    @property
    def active(self) -> bool:
        """False once persistence failed or was refused for this session."""
        return not self._disabled

    def _log(self, event: str, error_type: str = "") -> None:
        logger.warning(
            event,
            extra={
                "context": {
                    "session_id": self._session_id,
                    "error_type": error_type,
                }
            },
        )

    def _ensure_context(self) -> RecordingContext | None:
        if self._disabled:
            return None
        if self._context is None:
            try:
                self._context = self._recorder.begin(
                    self._owner_user_id,
                    agent_id=self._agent_id,
                    device_id=self._device_id,
                    locale=self._locale,
                    title="",
                    transcript_consent=True,
                    consent_version=self._consent_version,
                )
            except Exception as exc:
                self._disabled = True
                self._log("transcript_recording_unavailable", type(exc).__name__)
                return None
            if (
                not self._context.transcript_consent
                or self._context.conversation_id is None
            ):
                # Consent refused downstream (e.g. versioned grant missing):
                # treat as authoritative opt-out for the rest of the session.
                self._disabled = True
                self._log("transcript_recording_refused")
                return None
        return self._context

    def record_user_turn(
        self,
        *,
        raw_transcript: str,
        normalized_text: str,
        model_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persists one user utterance; failures are swallowed after logging."""
        context = self._ensure_context()
        if context is None or context.conversation_id is None:
            return
        turn = DialogueTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            role="user",
            content=model_text,
            transcript_pair=TranscriptPair(
                raw_transcript=raw_transcript,
                normalized_text=normalized_text,
                text_for_model=model_text,
            ),
            metadata=dict(metadata or {}),
        )
        try:
            stored = self._recorder.record_turn(context, turn)
        except Exception as exc:
            self._disabled = True
            self._log("transcript_turn_persist_failed", type(exc).__name__)
            return
        if stored is None:
            self._disabled = True
            self._log("transcript_turn_persist_refused")

    def record_assistant_turn(
        self, final_text: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Persists one final assistant reply (non-reasoning text only)."""
        if not final_text:
            return
        context = self._ensure_context()
        if context is None or context.conversation_id is None:
            return
        turn = DialogueTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            role="assistant",
            content=final_text,
            metadata=dict(metadata or {}),
        )
        try:
            stored = self._recorder.record_turn(context, turn)
        except Exception as exc:
            self._disabled = True
            self._log("transcript_turn_persist_failed", type(exc).__name__)
            return
        if stored is None:
            self._disabled = True
            self._log("transcript_turn_persist_refused")
