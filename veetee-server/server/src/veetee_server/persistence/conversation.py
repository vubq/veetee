"""Tenant-scoped conversation history persistence and retention purge.

Turn text is only ever written for conversations whose owner granted a
versioned transcript consent; raw audio is never persisted anywhere. Audit
metadata for conversation mutations carries identifiers and counts only,
never transcript content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from .database import PostgresDatabase
from .repository import record_audit

_CONVERSATION_COLUMNS = (
    "id, owner_user_id, agent_id, device_id, title, summary, locale, turn_count, "
    "status, transcript_consent, consent_version, started_at, ended_at, "
    "retention_until, deleted_at, updated_at"
)
_TURN_COLUMNS = (
    "id, conversation_id, owner_user_id, ordinal, turn_id, role, raw_transcript, "
    "normalized_text, model_text, content, tool_calls, tool_call_id, tool_name, "
    "provenance, created_at"
)


@dataclass(frozen=True, slots=True)
class StoredConversation:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID | None
    device_id: uuid.UUID | None
    title: str
    summary: str
    locale: str
    turn_count: int
    status: str
    transcript_consent: bool
    consent_version: str
    started_at: datetime
    ended_at: datetime | None
    retention_until: datetime | None
    deleted_at: datetime | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "device_id": str(self.device_id) if self.device_id else None,
            "title": self.title,
            "summary": self.summary,
            "locale": self.locale,
            "turn_count": self.turn_count,
            "status": self.status,
            "transcript_consent": self.transcript_consent,
            "consent_version": self.consent_version,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "retention_until": (
                self.retention_until.isoformat() if self.retention_until else None
            ),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class TurnInput:
    """Validated content of one conversation turn about to be persisted."""

    turn_id: str
    role: str
    content: str = ""
    raw_transcript: str = ""
    normalized_text: str = ""
    model_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str = ""
    tool_name: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredTurn:
    id: uuid.UUID
    conversation_id: uuid.UUID
    owner_user_id: uuid.UUID
    ordinal: int
    turn_id: str
    role: str
    raw_transcript: str
    normalized_text: str
    model_text: str
    content: str
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    tool_name: str
    provenance: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "ordinal": self.ordinal,
            "turn_id": self.turn_id,
            "role": self.role,
            "raw_transcript": self.raw_transcript,
            "normalized_text": self.normalized_text,
            "model_text": self.model_text,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "provenance": self.provenance,
            "created_at": self.created_at.isoformat(),
        }


def _conversation(row: tuple[Any, ...]) -> StoredConversation:
    return StoredConversation(
        id=cast(uuid.UUID, row[0]),
        owner_user_id=cast(uuid.UUID, row[1]),
        agent_id=cast(uuid.UUID | None, row[2]),
        device_id=cast(uuid.UUID | None, row[3]),
        title=cast(str, row[4]),
        summary=cast(str, row[5]),
        locale=cast(str, row[6]),
        turn_count=cast(int, row[7]),
        status=cast(str, row[8]),
        transcript_consent=cast(bool, row[9]),
        consent_version=cast(str, row[10]),
        started_at=cast(datetime, row[11]),
        ended_at=cast(datetime | None, row[12]),
        retention_until=cast(datetime | None, row[13]),
        deleted_at=cast(datetime | None, row[14]),
        updated_at=cast(datetime, row[15]),
    )


def _turn(row: tuple[Any, ...]) -> StoredTurn:
    return StoredTurn(
        id=cast(uuid.UUID, row[0]),
        conversation_id=cast(uuid.UUID, row[1]),
        owner_user_id=cast(uuid.UUID, row[2]),
        ordinal=cast(int, row[3]),
        turn_id=cast(str, row[4]),
        role=cast(str, row[5]),
        raw_transcript=cast(str, row[6]),
        normalized_text=cast(str, row[7]),
        model_text=cast(str, row[8]),
        content=cast(str, row[9]),
        tool_calls=cast(list[dict[str, Any]], row[10]),
        tool_call_id=cast(str, row[11]),
        tool_name=cast(str, row[12]),
        provenance=cast(dict[str, Any], row[13]),
        created_at=cast(datetime, row[14]),
    )


class ConversationRepository:
    """Conversation shells plus consent-gated per-turn transcripts."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_or_create(
        self,
        owner_user_id: uuid.UUID,
        *,
        agent_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        title: str = "",
        locale: str = "vi-VN",
        retention_days: int = 30,
    ) -> StoredConversation:
        """Creates (or returns the existing) conversation shell for a session.

        The shell itself carries no transcript; ``retention_until`` is stamped
        from ``retention_days`` so every conversation has a bounded lifetime.
        """
        with self.database.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(owner_user_id),))
            row = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} FROM veetee_conversations "
                "WHERE owner_user_id = %s AND agent_id IS NOT DISTINCT FROM %s "
                "AND device_id IS NOT DISTINCT FROM %s AND status = 'active' "
                "ORDER BY started_at DESC LIMIT 1",
                (owner_user_id, agent_id, device_id),
            ).fetchone()
            if row is not None:
                return _conversation(row)
            row = connection.execute(
                f"INSERT INTO veetee_conversations "
                f"(id, owner_user_id, agent_id, device_id, title, locale, "
                f"retention_until) VALUES (%s, %s, %s, %s, %s, %s, "
                f"now() + (%s * interval '1 second')) RETURNING {_CONVERSATION_COLUMNS}",
                (
                    uuid.uuid4(),
                    owner_user_id,
                    agent_id,
                    device_id,
                    title,
                    locale,
                    retention_days * 86400,
                ),
            ).fetchone()
            assert row is not None
            return _conversation(row)

    def get(
        self, owner_user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> StoredConversation | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} FROM veetee_conversations "
                "WHERE id = %s AND owner_user_id = %s",
                (conversation_id, owner_user_id),
            ).fetchone()
        return _conversation(row) if row else None

    def list_conversations(
        self, owner_user_id: uuid.UUID, limit: int = 200
    ) -> list[StoredConversation]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} FROM veetee_conversations "
                "WHERE owner_user_id = %s ORDER BY started_at DESC, id LIMIT %s",
                (owner_user_id, limit),
            ).fetchall()
        return [_conversation(row) for row in rows]

    def update(
        self,
        owner_user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        title: str | None = None,
        summary: str | None = None,
        transcript_consent: bool | None = None,
        consent_version: str | None = None,
        end: bool = False,
    ) -> StoredConversation | None:
        assignments: list[str] = ["updated_at = now()"]
        params: list[Any] = []
        if title is not None:
            assignments.append("title = %s")
            params.append(title)
        if summary is not None:
            assignments.append("summary = %s")
            params.append(summary)
        if transcript_consent is not None:
            assignments.append("transcript_consent = %s")
            params.append(transcript_consent)
        if consent_version is not None:
            assignments.append("consent_version = %s")
            params.append(consent_version)
        if end:
            assignments.extend(["status = 'ended'", "ended_at = COALESCE(ended_at, now())"])
        params.extend([conversation_id, owner_user_id])
        with self.database.connection() as connection:
            row = connection.execute(
                f"UPDATE veetee_conversations SET {', '.join(assignments)} "
                f"WHERE id = %s AND owner_user_id = %s RETURNING {_CONVERSATION_COLUMNS}",
                params,
            ).fetchone()
        return _conversation(row) if row else None

    def append_turn(
        self, owner_user_id: uuid.UUID, conversation_id: uuid.UUID, turn: TurnInput
    ) -> StoredTurn | None:
        """Appends one turn under the conversation ordinal lock.

        Returns ``None`` when the conversation does not belong to the owner.
        The caller must have verified transcript consent beforehand.
        """
        with self.database.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (str(conversation_id),)
            )
            owned = connection.execute(
                "SELECT transcript_consent, consent_version FROM veetee_conversations "
                "WHERE id = %s AND owner_user_id = %s",
                (conversation_id, owner_user_id),
            ).fetchone()
            if owned is None or not owned[0] or not owned[1]:
                return None
            ordinal_row = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM veetee_conversation_turns "
                "WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()
            assert ordinal_row is not None
            next_ordinal = cast(int, ordinal_row[0])
            row = connection.execute(
                f"INSERT INTO veetee_conversation_turns "
                f"(id, conversation_id, owner_user_id, ordinal, turn_id, role, "
                f"raw_transcript, normalized_text, model_text, content, tool_calls, "
                f"tool_call_id, tool_name, provenance) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"ON CONFLICT (conversation_id, turn_id) DO NOTHING "
                f"RETURNING {_TURN_COLUMNS}",
                (
                    uuid.uuid4(),
                    conversation_id,
                    owner_user_id,
                    next_ordinal,
                    turn.turn_id,
                    turn.role,
                    turn.raw_transcript,
                    turn.normalized_text,
                    turn.model_text,
                    turn.content,
                    Jsonb(turn.tool_calls),
                    turn.tool_call_id,
                    turn.tool_name,
                    Jsonb(turn.provenance),
                ),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    f"SELECT {_TURN_COLUMNS} FROM veetee_conversation_turns "
                    "WHERE conversation_id = %s AND turn_id = %s",
                    (conversation_id, turn.turn_id),
                ).fetchone()
                return _turn(existing) if existing else None
            connection.execute(
                "UPDATE veetee_conversations SET "
                "turn_count = (SELECT count(*) FROM veetee_conversation_turns "
                "WHERE conversation_id = %s), updated_at = now() WHERE id = %s",
                (conversation_id, conversation_id),
            )
            assert row is not None
            return _turn(row)

    def list_turns(
        self, owner_user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> list[StoredTurn]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM veetee_conversation_turns "
                "WHERE conversation_id = %s AND owner_user_id = %s ORDER BY ordinal",
                (conversation_id, owner_user_id),
            ).fetchall()
        return [_turn(row) for row in rows]

    def hard_delete(
        self, owner_user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> tuple[bool, int]:
        """Deletes the conversation and all turns in one transaction.

        Returns ``(deleted, turns_removed)``; audit metadata never includes
        transcript content.
        """
        with self.database.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (str(conversation_id),)
            )
            count_row = connection.execute(
                "SELECT count(*) FROM veetee_conversation_turns "
                "WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()
            turns_removed = cast(int, count_row[0]) if count_row else 0
            result = connection.execute(
                "DELETE FROM veetee_conversations WHERE id = %s AND owner_user_id = %s",
                (conversation_id, owner_user_id),
            )
            if result.rowcount != 1:
                return False, 0
            record_audit(
                self.database,
                owner_user_id,
                "conversation.hard_delete",
                "conversation",
                str(conversation_id),
                {"turns_removed": turns_removed},
                connection=connection,
            )
            return True, turns_removed


def purge_expired_conversations(
    database: PostgresDatabase, *, batch_size: int = 500
) -> int:
    """Batch-deletes conversations whose ``retention_until`` has passed.

    Idempotent: rerunning the purge removes nothing further. Each non-empty
    batch writes one redacted audit event; no transcript content is touched.
    """
    total = 0
    while True:
        with database.connection() as connection:
            rows = connection.execute(
                "DELETE FROM veetee_conversations c USING ("
                "SELECT id FROM veetee_conversations "
                "WHERE retention_until IS NOT NULL AND retention_until < now() "
                "ORDER BY retention_until LIMIT %s) victims "
                "WHERE c.id = victims.id RETURNING c.id",
                (batch_size,),
            ).fetchall()
            removed = len(rows)
            if removed:
                record_audit(
                    database,
                    None,
                    "conversation.retention_purge",
                    "conversation_batch",
                    f"{removed}",
                    {"deleted": removed},
                    connection=connection,
                )
        total += removed
        if removed < batch_size:
            return total
