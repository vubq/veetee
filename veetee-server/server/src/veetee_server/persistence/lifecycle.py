"""Tenant-scoped agent lifecycle persistence: templates, tags and snapshots.

Snapshots are append-only and enforced immutable at the database level
(see migration 006). Restores never mutate history: they record a
``pre_restore`` snapshot of the current configuration before overwriting it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import psycopg
from psycopg.types.json import Jsonb

from veetee_server.agent_config import (
    AGENT_CONFIG_SCHEMA_VERSION,
    canonical_agent_checksum,
)

from .database import PostgresDatabase
from .repository import (
    AGENT_CONFIG_FIELDS,
    AGENT_PROFILE_FIELDS,
    AGENT_SELECT_COLUMNS,
    AgentRepository,
    StoredAgent,
    agent_set_clause,
    record_audit,
)

_JSONB_CONFIG_FIELDS = {"tool_policy", "memory_policy"}

_TEMPLATE_COLUMNS = "id, owner_user_id, name, description, config, created_at, updated_at"
_TAG_COLUMNS = "id, owner_user_id, name, created_at"
_SNAPSHOT_COLUMNS = (
    "id, owner_user_id, agent_id, source_version, schema_version, checksum, "
    "config, reason, created_by, created_at"
)


@dataclass(frozen=True, slots=True)
class StoredAgentTemplate:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    description: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "config": self.config,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class StoredTag:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class StoredAgentSnapshot:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    source_version: int
    schema_version: str
    checksum: str
    config: dict[str, Any]
    reason: str
    created_by: uuid.UUID | None
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "agent_id": str(self.agent_id),
            "source_version": self.source_version,
            "schema_version": self.schema_version,
            "checksum": self.checksum,
            "config": self.config,
            "reason": self.reason,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat(),
        }


def _template(row: tuple[Any, ...]) -> StoredAgentTemplate:
    return StoredAgentTemplate(
        id=cast(uuid.UUID, row[0]),
        owner_user_id=cast(uuid.UUID, row[1]),
        name=cast(str, row[2]),
        description=cast(str, row[3]),
        config=cast(dict[str, Any], row[4]),
        created_at=cast(datetime, row[5]),
        updated_at=cast(datetime, row[6]),
    )


def _tag(row: tuple[Any, ...]) -> StoredTag:
    return StoredTag(
        id=cast(uuid.UUID, row[0]),
        owner_user_id=cast(uuid.UUID, row[1]),
        name=cast(str, row[2]),
        created_at=cast(datetime, row[3]),
    )


def _snapshot(row: tuple[Any, ...]) -> StoredAgentSnapshot:
    return StoredAgentSnapshot(
        id=cast(uuid.UUID, row[0]),
        owner_user_id=cast(uuid.UUID, row[1]),
        agent_id=cast(uuid.UUID, row[2]),
        source_version=cast(int, row[3]),
        schema_version=cast(str, row[4]),
        checksum=cast(str, row[5]),
        config=cast(dict[str, Any], row[6]),
        reason=cast(str, row[7]),
        created_by=cast(uuid.UUID | None, row[8]),
        created_at=cast(datetime, row[9]),
    )


class _RestoreConflict(Exception):
    """Internal signal: the conditional restore update lost a race."""


class AgentLifecycleRepository:
    """Templates, tags and immutable agent snapshots for one tenant at a time."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database
        self._agents = AgentRepository(database)

    # ------------------------------------------------------------------ tags

    def create_tag(
        self, owner_user_id: uuid.UUID, name: str
    ) -> tuple[StoredTag | None, str | None]:
        tag_id = uuid.uuid4()
        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"INSERT INTO veetee_agent_tags (id, owner_user_id, name) "
                    f"VALUES (%s, %s, %s) RETURNING {_TAG_COLUMNS}",
                    (tag_id, owner_user_id, name),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                return None, "duplicate_name"
            assert row is not None
            return _tag(row), None

    def list_tags(self, owner_user_id: uuid.UUID) -> list[StoredTag]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_TAG_COLUMNS} FROM veetee_agent_tags "
                "WHERE owner_user_id = %s ORDER BY name",
                (owner_user_id,),
            ).fetchall()
        return [_tag(row) for row in rows]

    def delete_tag(self, owner_user_id: uuid.UUID, tag_id: uuid.UUID) -> bool:
        with self.database.connection() as connection:
            result = connection.execute(
                "DELETE FROM veetee_agent_tags WHERE id = %s AND owner_user_id = %s",
                (tag_id, owner_user_id),
            )
        deleted = result.rowcount == 1
        if deleted:
            record_audit(
                self.database, owner_user_id, "agent.tag.delete", "agent_tag", str(tag_id)
            )
        return deleted

    def attach_tag(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID, tag_id: uuid.UUID
    ) -> bool:
        with self.database.connection() as connection:
            tag_ok = connection.execute(
                "SELECT 1 FROM veetee_agent_tags WHERE id = %s AND owner_user_id = %s",
                (tag_id, owner_user_id),
            ).fetchone()
            agent_ok = connection.execute(
                "SELECT 1 FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if tag_ok is None or agent_ok is None:
                return False
            connection.execute(
                "INSERT INTO veetee_agent_tag_links (tag_id, agent_id) VALUES (%s, %s) "
                "ON CONFLICT (tag_id, agent_id) DO NOTHING",
                (tag_id, agent_id),
            )
        return True

    def detach_tag(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID, tag_id: uuid.UUID
    ) -> bool:
        with self.database.connection() as connection:
            result = connection.execute(
                "DELETE FROM veetee_agent_tag_links l USING veetee_agent_tags t "
                "WHERE l.tag_id = t.id AND l.agent_id = %s AND l.tag_id = %s "
                "AND t.owner_user_id = %s",
                (agent_id, tag_id, owner_user_id),
            )
        return result.rowcount == 1

    def list_agent_tags(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredTag]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_TAG_COLUMNS} FROM veetee_agent_tags t "
                "JOIN veetee_agent_tag_links l ON l.tag_id = t.id "
                "WHERE t.owner_user_id = %s AND l.agent_id = %s ORDER BY t.name",
                (owner_user_id, agent_id),
            ).fetchall()
        return [_tag(row) for row in rows]

    # ------------------------------------------------------------- templates

    def create_template(
        self,
        owner_user_id: uuid.UUID,
        name: str,
        description: str,
        config: dict[str, Any],
    ) -> tuple[StoredAgentTemplate | None, str | None]:
        # The template carries its own name; the stored agent profile config is
        # kept name-free so creating an agent from it supplies the fresh name.
        stored_config = {key: value for key, value in config.items() if key != "name"}
        template_id = uuid.uuid4()
        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"INSERT INTO veetee_agent_templates "
                    f"(id, owner_user_id, name, description, config) "
                    f"VALUES (%s, %s, %s, %s, %s) RETURNING {_TEMPLATE_COLUMNS}",
                    (template_id, owner_user_id, name, description, Jsonb(stored_config)),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                return None, "duplicate_name"
            assert row is not None
            template = _template(row)
            record_audit(
                self.database,
                owner_user_id,
                "agent.template.create",
                "agent_template",
                str(template.id),
                connection=connection,
            )
            return template, None

    def list_templates(self, owner_user_id: uuid.UUID) -> list[StoredAgentTemplate]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM veetee_agent_templates "
                "WHERE owner_user_id = %s ORDER BY name",
                (owner_user_id,),
            ).fetchall()
        return [_template(row) for row in rows]

    def get_template(
        self, owner_user_id: uuid.UUID, template_id: uuid.UUID
    ) -> StoredAgentTemplate | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM veetee_agent_templates "
                "WHERE id = %s AND owner_user_id = %s",
                (template_id, owner_user_id),
            ).fetchone()
        return _template(row) if row else None

    def create_agent_from_template(
        self,
        owner_user_id: uuid.UUID,
        template_id: uuid.UUID,
        name: str,
    ) -> tuple[StoredAgent | None, str | None]:
        template = self.get_template(owner_user_id, template_id)
        if template is None:
            return None, "template_not_found"
        data = {**template.config, "name": name}
        stored, error = self._agents.create(owner_user_id, data)
        if error is not None or stored is None:
            return None, error
        record_audit(
            self.database,
            owner_user_id,
            "agent.create.from_template",
            "agent",
            str(stored.id),
            {"template_id": str(template.id)},
        )
        return stored, None

    # ------------------------------------------------------------- snapshots

    def _insert_snapshot(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        source_version: int,
        config: dict[str, Any],
        reason: str,
        created_by: uuid.UUID | None,
    ) -> StoredAgentSnapshot:
        row = connection.execute(
            f"INSERT INTO veetee_agent_snapshots "
            f"(id, owner_user_id, agent_id, source_version, schema_version, checksum, "
            f"config, reason, created_by) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_SNAPSHOT_COLUMNS}",
            (
                uuid.uuid4(),
                owner_user_id,
                agent_id,
                source_version,
                AGENT_CONFIG_SCHEMA_VERSION,
                canonical_agent_checksum(config),
                Jsonb(config),
                reason,
                created_by,
            ),
        ).fetchone()
        assert row is not None
        return _snapshot(row)

    @staticmethod
    def _current_config(agent: StoredAgent) -> dict[str, Any]:
        return {"name": agent.name, **agent.profile}

    def create_snapshot(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        *,
        reason: str = "manual",
        created_by: uuid.UUID | None = None,
    ) -> StoredAgentSnapshot | None:
        agent = self._agents.get(owner_user_id, agent_id)
        if agent is None:
            return None
        with self.database.connection() as connection:
            snapshot = self._insert_snapshot(
                connection,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
                source_version=agent.version,
                config=self._current_config(agent),
                reason=reason,
                created_by=created_by or owner_user_id,
            )
            record_audit(
                self.database,
                owner_user_id,
                "agent.snapshot.create",
                "agent_snapshot",
                str(snapshot.id),
                {"agent_id": str(agent_id), "reason": reason},
                connection=connection,
            )
            return snapshot

    def list_snapshots(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredAgentSnapshot]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM veetee_agent_snapshots "
                "WHERE owner_user_id = %s AND agent_id = %s "
                "ORDER BY created_at DESC, id",
                (owner_user_id, agent_id),
            ).fetchall()
        return [_snapshot(row) for row in rows]

    def get_snapshot(
        self, owner_user_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> StoredAgentSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM veetee_agent_snapshots "
                "WHERE id = %s AND owner_user_id = %s",
                (snapshot_id, owner_user_id),
            ).fetchone()
        return _snapshot(row) if row else None

    def restore_snapshot(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        snapshot_id: uuid.UUID,
        expected_agent_version: int,
        validate: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> tuple[StoredAgent | None, str | None]:
        """Restores ``snapshot_id`` atomically behind ``expected_agent_version``.

        The caller supplies ``validate`` mapping the snapshot config through the
        current API schema; returning ``None`` rejects restoration. Before the
        overwrite a ``pre_restore`` immutable snapshot of the current state is
        recorded inside the same transaction. Error codes: ``agent_not_found``,
        ``snapshot_not_found``, ``stale_version``, ``invalid_config`` and
        ``duplicate_name``.
        """
        with self.database.connection() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(agent_id),))
            row = connection.execute(
                f"SELECT {AGENT_SELECT_COLUMNS} FROM veetee_agents "
                "WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if row is None:
                return None, "agent_not_found"
            current = StoredAgent(
                cast(uuid.UUID, row[0]),
                cast(uuid.UUID, row[1]),
                cast(str, row[2]),
                cast(int, row[3]),
                dict(zip(AGENT_PROFILE_FIELDS, row[4:], strict=True)),
            )
            if current.version != expected_agent_version:
                return None, "stale_version"

            snap_row = connection.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM veetee_agent_snapshots "
                "WHERE id = %s AND agent_id = %s AND owner_user_id = %s",
                (snapshot_id, agent_id, owner_user_id),
            ).fetchone()
            if snap_row is None:
                return None, "snapshot_not_found"
            snapshot = _snapshot(snap_row)

            normalized = validate(snapshot.config)
            if normalized is None or set(normalized) != set(AGENT_CONFIG_FIELDS):
                return None, "invalid_config"

            values = [
                Jsonb(normalized[field])
                if field in _JSONB_CONFIG_FIELDS
                else normalized[field]
                for field in AGENT_CONFIG_FIELDS
            ]
            try:
                with connection.transaction():
                    pre_restore = self._insert_snapshot(
                        connection,
                        owner_user_id=owner_user_id,
                        agent_id=agent_id,
                        source_version=current.version,
                        config=self._current_config(current),
                        reason="pre_restore",
                        created_by=actor_user_id or owner_user_id,
                    )
                    result = connection.execute(
                        "UPDATE veetee_agents SET "
                        + agent_set_clause(AGENT_CONFIG_FIELDS)
                        + ", version = version + 1, updated_at = now() "
                        "WHERE id = %s AND owner_user_id = %s AND version = %s",
                        (*values, agent_id, owner_user_id, expected_agent_version),
                    )
                    if result.rowcount != 1:
                        raise _RestoreConflict
            except psycopg.errors.UniqueViolation:
                return None, "duplicate_name"
            except _RestoreConflict:
                return None, "stale_version"

            record_audit(
                self.database,
                actor_user_id or owner_user_id,
                "agent.restore",
                "agent",
                str(agent_id),
                {
                    "snapshot_id": str(snapshot.id),
                    "restored_source_version": snapshot.source_version,
                    "new_version": expected_agent_version + 1,
                    "pre_restore_snapshot_id": str(pre_restore.id),
                },
                connection=connection,
            )

            updated_row = connection.execute(
                f"SELECT {AGENT_SELECT_COLUMNS} FROM veetee_agents "
                "WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if updated_row is None:  # pragma: no cover - same transaction readback
                raise RuntimeError("Restored agent row vanished in the same transaction")
            restored = StoredAgent(
                cast(uuid.UUID, updated_row[0]),
                cast(uuid.UUID, updated_row[1]),
                cast(str, updated_row[2]),
                cast(int, updated_row[3]),
                dict(zip(AGENT_PROFILE_FIELDS, updated_row[4:], strict=True)),
            )
            return restored, None
