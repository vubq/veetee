"""Tenant-aware repositories for external integration endpoints and permissions.

M6.6 decision (locked): endpoint URLs are HTTPS-only and carry no credentials;
``auth_header_env`` stores only the *name* of the environment variable holding
a bearer token, never the token itself. Audit metadata contains identifiers
and permission flags only — never arguments or secret material.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import urlparse

from .database import PostgresDatabase
from .repository import record_audit

_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_MAX_URL_CHARS = 2048
_MAX_NAME_CHARS = 200


def validate_external_endpoint_url(url: str) -> str:
    """Validates a persisted integration URL without touching DNS.

    Rules: HTTPS only, no userinfo/credentials, explicit host, no fragment,
    port in range when present. Host allowlisting happens at call time via
    :class:`veetee_server.tools.ssrf.ExternalURLPolicy`.
    """
    cleaned = url.strip()
    if not cleaned or len(cleaned) > _MAX_URL_CHARS:
        raise ValueError("Endpoint URL must be 1..2048 characters")
    try:
        parsed = urlparse(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid endpoint URL: {exc}") from exc
    if parsed.scheme.casefold() != "https":
        raise ValueError("Endpoint URL must use HTTPS")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ValueError("Endpoint URL must not contain userinfo or credentials")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Endpoint URL must contain a valid host")
    if parsed.fragment:
        raise ValueError("Endpoint URL must not contain a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Endpoint URL port must be between 1 and 65535")
    return cleaned


@dataclass(frozen=True, slots=True)
class StoredExternalEndpoint:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    url: str
    auth_header_env: str | None
    enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredIntegrationPermission:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    endpoint_id: uuid.UUID
    can_list: bool
    can_call: bool
    rate_limit_calls: int
    rate_limit_window_seconds: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


_ENDPOINT_COLUMNS = (
    "id, owner_user_id, name, url, auth_header_env, enabled, version, "
    "created_at, updated_at"
)

_PERMISSION_COLUMNS = (
    "id, owner_user_id, agent_id, endpoint_id, can_list, can_call, "
    "rate_limit_calls, rate_limit_window_seconds, created_at, updated_at"
)


def _endpoint_from_row(row: tuple[object, ...]) -> StoredExternalEndpoint:
    return StoredExternalEndpoint(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        name=str(row[2]),
        url=str(row[3]),
        auth_header_env=str(row[4]) if row[4] is not None else None,
        enabled=bool(row[5]),
        version=int(str(row[6])),
        created_at=cast(datetime, row[7]),
        updated_at=cast(datetime, row[8]),
    )


def _permission_from_row(row: tuple[object, ...]) -> StoredIntegrationPermission:
    return StoredIntegrationPermission(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        agent_id=cast_uuid(row[2]),
        endpoint_id=cast_uuid(row[3]),
        can_list=bool(row[4]),
        can_call=bool(row[5]),
        rate_limit_calls=int(str(row[6])),
        rate_limit_window_seconds=int(str(row[7])),
        created_at=cast("datetime | None", row[8]) if row[8] is not None else None,
        updated_at=cast("datetime | None", row[9]) if row[9] is not None else None,
    )


def cast_uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class ToolingRepository:
    """PostgreSQL-backed repository for M6.6 tooling persistence."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    # ------------------------------------------------------------- endpoints

    def create_endpoint(
        self,
        owner_user_id: uuid.UUID,
        name: str,
        url: str,
        *,
        auth_header_env: str | None = None,
        enabled: bool = True,
    ) -> StoredExternalEndpoint:
        clean_name = name.strip()
        if not clean_name or len(clean_name) > _MAX_NAME_CHARS:
            raise ValueError("Endpoint name must be 1..200 characters")
        clean_url = validate_external_endpoint_url(url)
        clean_env: str | None = None
        if auth_header_env is not None and auth_header_env.strip():
            candidate = auth_header_env.strip()
            if not _ENV_NAME_PATTERN.match(candidate):
                raise ValueError(
                    "auth_header_env must look like an environment variable name"
                )
            clean_env = candidate

        endpoint_id = uuid.uuid4()
        with self.database.connection() as conn:
            duplicate = conn.execute(
                "SELECT id FROM veetee_external_endpoints "
                "WHERE owner_user_id = %s AND name = %s",
                (owner_user_id, clean_name),
            ).fetchone()
            if duplicate:
                raise ValueError(f"Endpoint named '{clean_name}' already exists")
            row = conn.execute(
                "INSERT INTO veetee_external_endpoints "
                "(id, owner_user_id, name, url, auth_header_env, enabled) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                f"RETURNING {_ENDPOINT_COLUMNS}",
                (endpoint_id, owner_user_id, clean_name, clean_url, clean_env, enabled),
            ).fetchone()
            assert row is not None
            stored = _endpoint_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "integration.endpoint.create",
                "external_endpoint",
                str(endpoint_id),
                {"name": clean_name},
                connection=conn,
            )
            return stored

    def get_endpoint(
        self, owner_user_id: uuid.UUID, endpoint_id: uuid.UUID
    ) -> StoredExternalEndpoint | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_ENDPOINT_COLUMNS} FROM veetee_external_endpoints "
                "WHERE id = %s AND owner_user_id = %s",
                (endpoint_id, owner_user_id),
            ).fetchone()
            return _endpoint_from_row(row) if row else None

    def list_endpoints(
        self, owner_user_id: uuid.UUID
    ) -> list[StoredExternalEndpoint]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_ENDPOINT_COLUMNS} FROM veetee_external_endpoints "
                "WHERE owner_user_id = %s ORDER BY created_at ASC, id ASC",
                (owner_user_id,),
            ).fetchall()
            return [_endpoint_from_row(row) for row in rows]

    def update_endpoint(
        self,
        owner_user_id: uuid.UUID,
        endpoint_id: uuid.UUID,
        *,
        name: str | None = None,
        url: str | None = None,
        auth_header_env: str | None = None,
        enabled: bool | None = None,
        expected_version: int | None = None,
    ) -> StoredExternalEndpoint:
        """Partially updates one endpoint; ``auth_header_env`` clears when null."""
        clean_name: str | None = None
        if name is not None:
            clean_name = name.strip()
            if not clean_name or len(clean_name) > _MAX_NAME_CHARS:
                raise ValueError("Endpoint name must be 1..200 characters")
        clean_url: str | None = None
        if url is not None:
            clean_url = validate_external_endpoint_url(url)
        final_env_input: str | None
        if auth_header_env is None:
            final_env_input = None
        else:
            candidate = auth_header_env.strip()
            if candidate and not _ENV_NAME_PATTERN.match(candidate):
                raise ValueError(
                    "auth_header_env must look like an environment variable name"
                )
            final_env_input = candidate or None

        with self.database.connection() as conn:
            current_row = conn.execute(
                f"SELECT {_ENDPOINT_COLUMNS} FROM veetee_external_endpoints "
                "WHERE id = %s AND owner_user_id = %s",
                (endpoint_id, owner_user_id),
            ).fetchone()
            if not current_row:
                raise KeyError("Endpoint not found")
            current = _endpoint_from_row(current_row)
            if expected_version is not None and current.version != expected_version:
                raise ValueError("Optimistic lock failure")

            final_name = clean_name if clean_name is not None else current.name
            final_url = clean_url if clean_url is not None else current.url
            final_enabled = enabled if enabled is not None else current.enabled

            row = conn.execute(
                "UPDATE veetee_external_endpoints "
                "SET name = %s, url = %s, auth_header_env = %s, enabled = %s, "
                "version = version + 1, updated_at = now() "
                "WHERE id = %s AND owner_user_id = %s AND version = %s "
                f"RETURNING {_ENDPOINT_COLUMNS}",
                (
                    final_name,
                    final_url,
                    final_env_input,
                    final_enabled,
                    endpoint_id,
                    owner_user_id,
                    current.version,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("Optimistic lock failure")
            stored = _endpoint_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "integration.endpoint.update",
                "external_endpoint",
                str(endpoint_id),
                {"version": stored.version},
                connection=conn,
            )
            return stored

    def delete_endpoint(self, owner_user_id: uuid.UUID, endpoint_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            result = conn.execute(
                "DELETE FROM veetee_external_endpoints "
                "WHERE id = %s AND owner_user_id = %s",
                (endpoint_id, owner_user_id),
            )
            deleted = result.rowcount > 0
            if deleted:
                record_audit(
                    self.database,
                    owner_user_id,
                    "integration.endpoint.delete",
                    "external_endpoint",
                    str(endpoint_id),
                    connection=conn,
                )
            return bool(deleted)

    # ------------------------------------------------------------ permissions

    def put_permission(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        endpoint_id: uuid.UUID,
        *,
        can_list: bool,
        can_call: bool,
        rate_limit_calls: int = 30,
        rate_limit_window_seconds: int = 60,
    ) -> StoredIntegrationPermission:
        if rate_limit_calls <= 0 or rate_limit_window_seconds <= 0:
            raise ValueError("Rate limit bounds must be positive")
        with self.database.connection() as conn:
            agent_ok = conn.execute(
                "SELECT id FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if not agent_ok:
                raise KeyError("Agent not found for current tenant")
            endpoint_ok = conn.execute(
                "SELECT id FROM veetee_external_endpoints "
                "WHERE id = %s AND owner_user_id = %s",
                (endpoint_id, owner_user_id),
            ).fetchone()
            if not endpoint_ok:
                raise KeyError("Endpoint not found for current tenant")

            row = conn.execute(
                "INSERT INTO veetee_agent_integration_permissions "
                "(id, owner_user_id, agent_id, endpoint_id, can_list, can_call, "
                "rate_limit_calls, rate_limit_window_seconds) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (agent_id, endpoint_id) DO UPDATE SET "
                "can_list = EXCLUDED.can_list, can_call = EXCLUDED.can_call, "
                "rate_limit_calls = EXCLUDED.rate_limit_calls, "
                "rate_limit_window_seconds = EXCLUDED.rate_limit_window_seconds, "
                "updated_at = now() "
                f"RETURNING {_PERMISSION_COLUMNS}",
                (
                    uuid.uuid4(),
                    owner_user_id,
                    agent_id,
                    endpoint_id,
                    can_list,
                    can_call,
                    rate_limit_calls,
                    rate_limit_window_seconds,
                ),
            ).fetchone()
            assert row is not None
            stored = _permission_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "integration.permission.upsert",
                "agent_integration_permission",
                str(stored.id),
                {
                    "agent_id": str(agent_id),
                    "endpoint_id": str(endpoint_id),
                    "can_list": can_list,
                    "can_call": can_call,
                },
                connection=conn,
            )
            return stored

    def list_agent_permissions(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredIntegrationPermission]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_PERMISSION_COLUMNS} FROM veetee_agent_integration_permissions "
                "WHERE owner_user_id = %s AND agent_id = %s "
                "ORDER BY created_at ASC, id ASC",
                (owner_user_id, agent_id),
            ).fetchall()
            return [_permission_from_row(row) for row in rows]

    def get_permission(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        endpoint_id: uuid.UUID,
    ) -> StoredIntegrationPermission | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_PERMISSION_COLUMNS} FROM veetee_agent_integration_permissions "
                "WHERE owner_user_id = %s AND agent_id = %s AND endpoint_id = %s",
                (owner_user_id, agent_id, endpoint_id),
            ).fetchone()
            return _permission_from_row(row) if row else None

    def delete_permission(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        endpoint_id: uuid.UUID,
    ) -> bool:
        with self.database.connection() as conn:
            result = conn.execute(
                "DELETE FROM veetee_agent_integration_permissions "
                "WHERE owner_user_id = %s AND agent_id = %s AND endpoint_id = %s",
                (owner_user_id, agent_id, endpoint_id),
            )
            deleted = result.rowcount > 0
            if deleted:
                record_audit(
                    self.database,
                    owner_user_id,
                    "integration.permission.delete",
                    "agent_integration_permission",
                    str(endpoint_id),
                    {"agent_id": str(agent_id)},
                    connection=conn,
                )
            return bool(deleted)
