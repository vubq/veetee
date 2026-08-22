"""Tenant-aware repositories for Correction Sets, Rules, and Agent Context Provider Configs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from psycopg.types.json import Jsonb

from .database import PostgresDatabase
from .repository import record_audit


@dataclass(frozen=True, slots=True)
class StoredCorrectionSet:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID | None
    name: str
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredCorrectionRule:
    id: uuid.UUID
    set_id: uuid.UUID
    owner_user_id: uuid.UUID
    ordinal: int
    rule_type: Literal["exact", "phrase"]
    pattern: str
    replacement: str
    case_sensitive: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredContextProviderConfig:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    provider_type: Literal["runtime", "memory", "knowledge_fts", "weather"]
    enabled: bool
    ordinal: int
    timeout_ms: int
    cache_ttl_seconds: int
    version: int
    config: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


_CORRECTION_SET_COLUMNS = (
    "id, owner_user_id, agent_id, name, enabled, version, created_at, updated_at"
)


def cast_uuid(val: Any) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _set_from_row(row: tuple[Any, ...]) -> StoredCorrectionSet:
    return StoredCorrectionSet(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        agent_id=cast_uuid(row[2]) if row[2] else None,
        name=cast(str, row[3]),
        enabled=bool(row[4]),
        version=cast(int, row[5]),
        created_at=cast(datetime, row[6]),
        updated_at=cast(datetime, row[7]),
    )


def _rule_from_row(row: tuple[Any, ...]) -> StoredCorrectionRule:
    return StoredCorrectionRule(
        id=cast_uuid(row[0]),
        set_id=cast_uuid(row[1]),
        owner_user_id=cast_uuid(row[2]),
        ordinal=cast(int, row[3]),
        rule_type=cast(Literal["exact", "phrase"], row[4]),
        pattern=cast(str, row[5]),
        replacement=cast(str, row[6]),
        case_sensitive=bool(row[7]),
        enabled=bool(row[8]),
        created_at=cast(datetime, row[9]),
        updated_at=cast(datetime, row[10]),
    )


_RULE_COLUMNS = (
    "id, set_id, owner_user_id, ordinal, rule_type, pattern, replacement, "
    "case_sensitive, enabled, created_at, updated_at"
)


def _provider_config_from_row(row: tuple[Any, ...]) -> StoredContextProviderConfig:
    return StoredContextProviderConfig(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        agent_id=cast_uuid(row[2]),
        provider_type=cast(
            Literal["runtime", "memory", "knowledge_fts", "weather"], row[3]
        ),
        enabled=bool(row[4]),
        ordinal=cast(int, row[5]),
        timeout_ms=cast(int, row[6]),
        cache_ttl_seconds=cast(int, row[7]),
        version=cast(int, row[8]),
        config=cast(dict[str, Any], row[9]),
        created_at=cast(datetime | None, row[10]),
        updated_at=cast(datetime | None, row[11]),
    )


_PROVIDER_CONFIG_COLUMNS = (
    "id, owner_user_id, agent_id, provider_type, enabled, ordinal, timeout_ms, "
    "cache_ttl_seconds, version, config, created_at, updated_at"
)


class CorrectionRepository:
    """PostgreSQL-backed repository for correction sets and rule engine persistence."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create_set(
        self,
        owner_user_id: uuid.UUID,
        name: str,
        agent_id: uuid.UUID | None = None,
        enabled: bool = True,
    ) -> StoredCorrectionSet:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Correction set name cannot be empty")
        set_id = uuid.uuid4()
        with self.database.connection() as conn:
            dup = conn.execute(
                "SELECT id FROM veetee_correction_sets "
                "WHERE owner_user_id = %s AND name = %s",
                (owner_user_id, clean_name),
            ).fetchone()
            if dup:
                raise ValueError(f"Correction set named '{clean_name}' already exists")
            if agent_id is not None:
                agent_ok = conn.execute(
                    "SELECT id FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                    (agent_id, owner_user_id),
                ).fetchone()
                if not agent_ok:
                    raise KeyError("Agent not found for current tenant")

            row = conn.execute(
                "INSERT INTO veetee_correction_sets "
                "(id, owner_user_id, agent_id, name, enabled) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_CORRECTION_SET_COLUMNS}",
                (set_id, owner_user_id, agent_id, clean_name, enabled),
            ).fetchone()
            assert row is not None
            cset = _set_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "correction.set.create",
                "correction_set",
                str(set_id),
                {"name": clean_name},
                connection=conn,
            )
            return cset

    def get_set(
        self, owner_user_id: uuid.UUID, set_id: uuid.UUID
    ) -> StoredCorrectionSet | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_CORRECTION_SET_COLUMNS} "
                "FROM veetee_correction_sets WHERE id = %s AND owner_user_id = %s",
                (set_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            return _set_from_row(row)

    def list_sets(self, owner_user_id: uuid.UUID) -> list[StoredCorrectionSet]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_CORRECTION_SET_COLUMNS} "
                "FROM veetee_correction_sets WHERE owner_user_id = %s ORDER BY name ASC",
                (owner_user_id,),
            ).fetchall()
            return [_set_from_row(r) for r in rows]

    def update_set(
        self,
        owner_user_id: uuid.UUID,
        set_id: uuid.UUID,
        name: str | None = None,
        enabled: bool | None = None,
        expected_version: int | None = None,
    ) -> StoredCorrectionSet:
        with self.database.connection() as conn:
            current_row = conn.execute(
                f"SELECT {_CORRECTION_SET_COLUMNS} FROM veetee_correction_sets "
                "WHERE id = %s AND owner_user_id = %s",
                (set_id, owner_user_id),
            ).fetchone()
            current = _set_from_row(current_row) if current_row else None
            if not current:
                raise KeyError(f"Correction set {set_id} not found")
            if expected_version is not None and current.version != expected_version:
                raise ValueError(
                    "Optimistic lock failure: expected version "
                    f"{expected_version}, got {current.version}"
                )

            new_name = name.strip() if name is not None else current.name
            new_enabled = enabled if enabled is not None else current.enabled

            row = conn.execute(
                "UPDATE veetee_correction_sets "
                "SET name = %s, enabled = %s, version = version + 1, updated_at = now() "
                "WHERE id = %s AND owner_user_id = %s AND version = %s "
                f"RETURNING {_CORRECTION_SET_COLUMNS}",
                (new_name, new_enabled, set_id, owner_user_id, current.version),
            ).fetchone()
            if row is None:
                raise ValueError("Optimistic lock failure")
            cset = _set_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "correction.set.update",
                "correction_set",
                str(set_id),
                {"version": cset.version},
                connection=conn,
            )
            return cset

    def delete_set(self, owner_user_id: uuid.UUID, set_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "DELETE FROM veetee_correction_sets WHERE id = %s AND owner_user_id = %s",
                (set_id, owner_user_id),
            )
            deleted = res.rowcount > 0
            if deleted:
                record_audit(
                    self.database,
                    owner_user_id,
                    "correction.set.delete",
                    "correction_set",
                    str(set_id),
                    connection=conn,
                )
            return bool(deleted)

    def add_rule(
        self,
        owner_user_id: uuid.UUID,
        set_id: uuid.UUID,
        ordinal: int,
        rule_type: Literal["exact", "phrase"],
        pattern: str,
        replacement: str,
        case_sensitive: bool = False,
        enabled: bool = True,
        expected_set_version: int | None = None,
    ) -> StoredCorrectionRule:
        clean_pattern = pattern.strip()
        if not clean_pattern:
            raise ValueError("Rule pattern cannot be empty")
        if rule_type not in ("exact", "phrase"):
            raise ValueError(f"Invalid rule_type: {rule_type}")
        if ordinal < 0:
            raise ValueError("Ordinal cannot be negative")
        if len(pattern) > 500 or len(replacement) > 500:
            raise ValueError("Pattern and replacement must stay within 500 characters")

        rule_id = uuid.uuid4()
        with self.database.connection() as conn:
            set_row = conn.execute(
                f"SELECT {_CORRECTION_SET_COLUMNS} FROM veetee_correction_sets "
                "WHERE id = %s AND owner_user_id = %s FOR UPDATE",
                (set_id, owner_user_id),
            ).fetchone()
            cset = _set_from_row(set_row) if set_row else None
            if not cset:
                raise KeyError(f"Correction set {set_id} not found")
            if expected_set_version is not None and cset.version != expected_set_version:
                raise ValueError("Optimistic lock failure")

            row = conn.execute(
                "INSERT INTO veetee_correction_rules "
                "(id, set_id, owner_user_id, ordinal, rule_type, pattern, replacement, "
                "case_sensitive, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {_RULE_COLUMNS}",
                (
                    rule_id,
                    set_id,
                    owner_user_id,
                    ordinal,
                    rule_type,
                    clean_pattern,
                    replacement,
                    case_sensitive,
                    enabled,
                ),
            ).fetchone()
            assert row is not None
            # Bump set version so runtime caches can invalidate deterministically.
            conn.execute(
                "UPDATE veetee_correction_sets SET version = version + 1, "
                "updated_at = now() WHERE id = %s",
                (set_id,),
            )
            record_audit(
                self.database,
                owner_user_id,
                "correction.rule.add",
                "correction_rule",
                str(rule_id),
                {"set_id": str(set_id), "ordinal": ordinal, "rule_type": rule_type},
                connection=conn,
            )
            return _rule_from_row(row)

    def list_rules(
        self, owner_user_id: uuid.UUID, set_id: uuid.UUID
    ) -> list[StoredCorrectionRule]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_RULE_COLUMNS} FROM veetee_correction_rules "
                "WHERE set_id = %s AND owner_user_id = %s "
                "ORDER BY ordinal ASC, created_at ASC",
                (set_id, owner_user_id),
            ).fetchall()
            return [_rule_from_row(r) for r in rows]

    def list_active_rules(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredCorrectionRule]:
        """Returns enabled global and agent rules in stable set/rule order."""
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT r.{_RULE_COLUMNS.replace(', ', ', r.')} "
                "FROM veetee_correction_rules r "
                "JOIN veetee_correction_sets s ON s.id = r.set_id "
                "AND s.owner_user_id = r.owner_user_id "
                "WHERE r.owner_user_id = %s AND s.enabled AND r.enabled "
                "AND (s.agent_id IS NULL OR s.agent_id = %s) "
                "ORDER BY s.name ASC, r.ordinal ASC, r.id ASC",
                (owner_user_id, agent_id),
            ).fetchall()
            return [_rule_from_row(row) for row in rows]

    def get_rule(
        self, owner_user_id: uuid.UUID, rule_id: uuid.UUID
    ) -> StoredCorrectionRule | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_RULE_COLUMNS} FROM veetee_correction_rules "
                "WHERE id = %s AND owner_user_id = %s",
                (rule_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            return _rule_from_row(row)

    def delete_rule(self, owner_user_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            row = conn.execute(
                "DELETE FROM veetee_correction_rules WHERE id = %s AND owner_user_id = %s "
                "RETURNING set_id",
                (rule_id, owner_user_id),
            ).fetchone()
            if row is not None:
                set_id = row[0]
                conn.execute(
                    "UPDATE veetee_correction_sets SET version = version + 1, "
                    "updated_at = now() WHERE id = %s AND owner_user_id = %s",
                    (set_id, owner_user_id),
                )
                record_audit(
                    self.database,
                    owner_user_id,
                    "correction.rule.delete",
                    "correction_rule",
                    str(rule_id),
                    {"set_id": str(set_id)},
                    connection=conn,
                )
                return True
            return False


class ContextProviderConfigRepository:
    """PostgreSQL-backed repository for agent context provider assignments."""

    PROVIDER_TYPES = ("runtime", "memory", "knowledge_fts", "weather")

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def upsert_config(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        provider_type: Literal["runtime", "memory", "knowledge_fts", "weather"],
        enabled: bool = True,
        ordinal: int = 0,
        timeout_ms: int = 2000,
        cache_ttl_seconds: int = 0,
        config: dict[str, Any] | None = None,
    ) -> StoredContextProviderConfig:
        if provider_type not in self.PROVIDER_TYPES:
            raise ValueError(f"Invalid provider_type: {provider_type}")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds cannot be negative")

        cfg_id = uuid.uuid4()
        with self.database.connection() as conn:
            agent_ok = conn.execute(
                "SELECT id FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if not agent_ok:
                raise KeyError("Agent not found for current tenant")

            row = conn.execute(
                "INSERT INTO veetee_agent_context_providers "
                "(id, owner_user_id, agent_id, provider_type, enabled, ordinal, "
                "timeout_ms, cache_ttl_seconds, config) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (agent_id, provider_type) DO UPDATE SET "
                "enabled = EXCLUDED.enabled, ordinal = EXCLUDED.ordinal, "
                "timeout_ms = EXCLUDED.timeout_ms, "
                "cache_ttl_seconds = EXCLUDED.cache_ttl_seconds, "
                "version = veetee_agent_context_providers.version + 1, "
                "config = EXCLUDED.config, updated_at = now() "
                f"RETURNING {_PROVIDER_CONFIG_COLUMNS}",
                (
                    cfg_id,
                    owner_user_id,
                    agent_id,
                    provider_type,
                    enabled,
                    ordinal,
                    timeout_ms,
                    cache_ttl_seconds,
                    Jsonb(config or {}),
                ),
            ).fetchone()
            assert row is not None
            result = _provider_config_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "context_provider.upsert",
                "context_provider_config",
                str(result.id),
                {"agent_id": str(agent_id), "provider_type": provider_type},
                connection=conn,
            )
            return result

    def list_agent_configs(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredContextProviderConfig]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_PROVIDER_CONFIG_COLUMNS} "
                "FROM veetee_agent_context_providers "
                "WHERE agent_id = %s AND owner_user_id = %s ORDER BY ordinal ASC",
                (agent_id, owner_user_id),
            ).fetchall()
            return [_provider_config_from_row(r) for r in rows]

    def delete_config(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID, provider_type: str
    ) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "DELETE FROM veetee_agent_context_providers "
                "WHERE agent_id = %s AND owner_user_id = %s AND provider_type = %s",
                (agent_id, owner_user_id, provider_type),
            )
            return res.rowcount > 0
