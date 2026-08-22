"""Tenant-aware repositories for users and immutable agent configuration snapshots."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, cast

from psycopg.types.json import Jsonb

from .database import PostgresDatabase


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = encoded.split("$")
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class StoredAgent:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    version: int
    profile: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "version": self.version,
            **self.profile,
        }


class UserRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def ensure_bootstrap(self, email: str, password: str) -> uuid.UUID:
        if not email or not password:
            raise ValueError("Bootstrap credentials must be provided through environment")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, role FROM veetee_users WHERE email = %s FOR UPDATE", (email,)
            ).fetchone()
            if row:
                if row[1] != "admin":
                    connection.execute(
                        "UPDATE veetee_users SET role = 'admin', updated_at = now() WHERE id = %s",
                        (row[0],),
                    )
                    connection.execute(
                        "INSERT INTO veetee_audit_events "
                        "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                        "VALUES (%s, %s, 'identity.bootstrap_admin_promoted', 'user', %s, %s)",
                        (
                            uuid.uuid4(),
                            row[0],
                            str(row[0]),
                            Jsonb({"configured_identity": email}),
                        ),
                    )
                return cast(uuid.UUID, row[0])
            user_id = uuid.uuid4()
            connection.execute(
                "INSERT INTO veetee_users (id, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                (user_id, email, hash_password(password), "admin"),
            )
            connection.execute(
                "INSERT INTO veetee_audit_events "
                "(id, actor_user_id, action, resource_type, resource_id, metadata) "
                "VALUES (%s, %s, 'identity.bootstrap_admin_created', 'user', %s, %s)",
                (uuid.uuid4(), user_id, str(user_id), Jsonb({"configured_identity": email})),
            )
            return user_id

    def authenticate(self, email: str, password: str) -> tuple[uuid.UUID, str] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, password_hash FROM veetee_users WHERE email = %s", (email,)
            ).fetchone()
            if not row or not verify_password(password, cast(str, row[1])):
                return None
            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            connection.execute(
                "INSERT INTO veetee_sessions (id, user_id, token_hash, expires_at) "
                "VALUES (%s, %s, %s, now() + interval '12 hours')",
                (uuid.uuid4(), row[0], token_hash),
            )
            return cast(uuid.UUID, row[0]), raw_token

    def resolve_token(self, raw_token: str) -> uuid.UUID | None:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM veetee_sessions "
                "WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()",
                (token_hash,),
            ).fetchone()
            return cast(uuid.UUID, row[0]) if row else None

    def get_role(self, user_id: uuid.UUID) -> str:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT role FROM veetee_users WHERE id = %s", (user_id,)
            ).fetchone()
            return cast(str, row[0]) if row else "owner"


def record_audit(
    database: PostgresDatabase,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Writes redacted control-plane metadata; callers never pass credentials."""
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO veetee_audit_events "
            "(id, actor_user_id, action, resource_type, resource_id, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                actor_user_id,
                action,
                resource_type,
                resource_id,
                Jsonb(metadata or {}),
            ),
        )


class AgentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list(self, owner_user_id: uuid.UUID) -> list[StoredAgent]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT id, owner_user_id, name, version, role_prompt, personality, "
                "address_style, language, detail_level, response_style, model_id, voice_id, "
                "intent_strategy, memory_enabled, memory_min_confidence, "
                "tool_policy, memory_policy "
                "FROM veetee_agents WHERE owner_user_id = %s ORDER BY created_at, id",
                (owner_user_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> StoredAgent | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, owner_user_id, name, version, role_prompt, personality, "
                "address_style, language, detail_level, response_style, model_id, voice_id, "
                "intent_strategy, memory_enabled, memory_min_confidence, "
                "tool_policy, memory_policy "
                "FROM veetee_agents WHERE owner_user_id = %s AND id = %s",
                (owner_user_id, agent_id),
            ).fetchone()
        return self._row(row) if row else None

    def snapshot(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> Any:
        from veetee_server.agents import snapshot_from_agent

        agent = self.get(owner_user_id, agent_id)
        return snapshot_from_agent(agent) if agent is not None else None

    def create(self, owner_user_id: uuid.UUID, data: dict[str, Any]) -> StoredAgent:
        agent_id = uuid.uuid4()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO veetee_agents (id, owner_user_id, name, role_prompt, personality, "
                "address_style, language, detail_level, response_style, model_id, voice_id, "
                "intent_strategy, memory_enabled, memory_min_confidence, "
                "tool_policy, memory_policy) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    agent_id,
                    owner_user_id,
                    data["name"],
                    data["role_prompt"],
                    data["personality"],
                    data["address_style"],
                    data["language"],
                    data["detail_level"],
                    data["response_style"],
                    data["model_id"],
                    data["voice_id"],
                    data["intent_strategy"],
                    data["memory_enabled"],
                    data["memory_min_confidence"],
                    Jsonb(data["tool_policy"]),
                    Jsonb(data["memory_policy"]),
                ),
            )
        return self.get(owner_user_id, agent_id)  # type: ignore[return-value]

    def update(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        expected_version: int,
        data: dict[str, Any],
    ) -> StoredAgent | None:
        fields = [
            "name",
            "role_prompt",
            "personality",
            "address_style",
            "language",
            "detail_level",
            "response_style",
            "model_id",
            "voice_id",
            "intent_strategy",
            "memory_enabled",
            "memory_min_confidence",
            "tool_policy",
            "memory_policy",
        ]
        values = [
            Jsonb(data[field]) if field in {"tool_policy", "memory_policy"} else data[field]
            for field in fields
        ]
        with self.database.connection() as connection:
            result = connection.execute(
                "UPDATE veetee_agents SET "
                + ", ".join(f"{field} = %s" for field in fields)
                + ", version = version + 1, updated_at = now() "
                "WHERE id = %s AND owner_user_id = %s AND version = %s",
                (*values, agent_id, owner_user_id, expected_version),
            )
            if result.rowcount != 1:
                return None
        return self.get(owner_user_id, agent_id)

    def delete(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        with self.database.connection() as connection:
            result = connection.execute(
                "DELETE FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            )
        return result.rowcount == 1

    @staticmethod
    def _row(row: tuple[Any, ...]) -> StoredAgent:
        profile = dict(
            zip(
                (
                    "role_prompt",
                    "personality",
                    "address_style",
                    "language",
                    "detail_level",
                    "response_style",
                    "model_id",
                    "voice_id",
                    "intent_strategy",
                    "memory_enabled",
                    "memory_min_confidence",
                    "tool_policy",
                    "memory_policy",
                ),
                row[4:],
                strict=True,
            )
        )
        return StoredAgent(row[0], row[1], row[2], row[3], profile)
