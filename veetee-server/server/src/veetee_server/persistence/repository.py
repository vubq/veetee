"""Tenant-aware repositories for users and immutable agent configuration snapshots."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NamedTuple, cast

import psycopg
from psycopg.types.json import Jsonb

from .database import PostgresDatabase


def parse_semver(v_str: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", v_str)]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    return tuple(nums) if nums else (0,)


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


def hash_login_identifier(email: str) -> str:
    """Redacted correlation key: SHA-256 of the normalized login identifier.

    Raw emails must never reach persistence or audit records; callers store and
    log only this hash.
    """
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


# Pre-computed scrypt hash so a login attempt against an unknown account burns
# comparable verification time to an existing account; response latency must
# not reveal whether the submitted address exists.
_TIMING_EQUALIZER_HASH = hash_password("veetee-login-timing-equalizer")


class LoginAttempt(NamedTuple):
    """Outcome of one throttled authentication attempt."""

    outcome: Literal["success", "invalid_credentials", "rate_limited"]
    user_id: uuid.UUID | None
    access_token: str | None
    retry_after_seconds: int


#: Persisted agent profile columns shared by every agent read/write path so
#: repositories cannot drift apart on the stored schema.
AGENT_PROFILE_FIELDS: tuple[str, ...] = (
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
)

AGENT_SELECT_COLUMNS = (
    "id, owner_user_id, name, version, " + ", ".join(AGENT_PROFILE_FIELDS)
)

#: Full agent configuration columns including the tenant-unique name.
AGENT_CONFIG_FIELDS: tuple[str, ...] = ("name", *AGENT_PROFILE_FIELDS)


def agent_set_clause(fields: tuple[str, ...]) -> str:
    return ", ".join(f"{field} = %s" for field in fields)


#: Persisted device columns shared by every device read/write path so the
#: gateway binding resolution, control-plane API and lifecycle repository
#: cannot drift apart on the stored schema (M6.2 adds transcript consent).
DEVICE_SELECT_COLUMNS = (
    "id, owner_user_id, agent_id, device_id, alias, board, chip, "
    "partition_name, firmware_version, client_id, "
    "transcript_consent, consent_version, consent_policy_version, "
    "last_seen_at, created_at, updated_at"
)


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


@dataclass(frozen=True, slots=True)
class Actor:
    """Typed authentication context containing identity, role, and lifecycle status."""

    user_id: uuid.UUID
    role: str
    status: str

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class UserRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def ensure_bootstrap(self, email: str, password: str) -> uuid.UUID:
        if not email or not password:
            raise ValueError("Bootstrap credentials must be provided through environment")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT id, role FROM veetee_users WHERE email = %s", (email,)
            ).fetchone()
            if row:
                if cast(str, row[1]) != "admin":
                    connection.execute(
                        "UPDATE veetee_users SET role = 'admin', "
                        "version = version + 1, updated_at = now() WHERE id = %s",
                        (row[0],),
                    )
                return cast(uuid.UUID, row[0])
            user_id = uuid.uuid4()
            connection.execute(
                "INSERT INTO veetee_users (id, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                (user_id, email, hash_password(password), "admin"),
            )
            return user_id

    def authenticate(
        self,
        email: str,
        password: str,
        *,
        rate_limit: int,
        rate_window_seconds: int,
    ) -> LoginAttempt:
        """Authenticates under a persisted per-identifier failure quota.

        The quota check runs before credential verification and keys on the
        redacted identifier hash, so responses never reveal whether an account
        exists. Failed attempts are recorded inside the same advisory-locked
        transaction; successful logins never consume quota.
        """
        identifier_hash = hash_login_identifier(email)
        with self.database.connection() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (identifier_hash,)
            )
            connection.execute(
                "DELETE FROM veetee_login_attempts "
                "WHERE attempted_at <= now() - (%s * interval '1 second')",
                (rate_window_seconds,),
            )
            row = connection.execute(
                "SELECT count(*), min(attempted_at) FROM veetee_login_attempts "
                "WHERE email_hash = %s",
                (identifier_hash,),
            ).fetchone()
            recent_failures = cast(int, row[0]) if row else 0
            if recent_failures >= rate_limit:
                return LoginAttempt(
                    "rate_limited", None, None, self._retry_after_seconds(row, rate_window_seconds)
                )

            # Migration 005 guarantees the status column with default 'active'.
            user_row = connection.execute(
                "SELECT id, password_hash, status FROM veetee_users WHERE email = %s",
                (email,),
            ).fetchone()

            if user_row is None:
                # Equalize verification cost with the known-account path.
                verify_password(password, _TIMING_EQUALIZER_HASH)
            elif cast(str, user_row[2]) == "suspended":
                verify_password(password, cast(str, user_row[1]))
                user_row = None
            elif not verify_password(password, cast(str, user_row[1])):
                user_row = None

            if user_row is None:
                connection.execute(
                    "INSERT INTO veetee_login_attempts (id, email_hash) VALUES (%s, %s)",
                    (uuid.uuid4(), identifier_hash),
                )
                return LoginAttempt("invalid_credentials", None, None, 0)

            connection.execute(
                "UPDATE veetee_users SET last_login_at = now() WHERE id = %s",
                (user_row[0],),
            )

            raw_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            connection.execute(
                "INSERT INTO veetee_sessions (id, user_id, token_hash, expires_at) "
                "VALUES (%s, %s, %s, now() + interval '12 hours')",
                (uuid.uuid4(), user_row[0], token_hash),
            )
            return LoginAttempt("success", cast(uuid.UUID, user_row[0]), raw_token, 0)

    @staticmethod
    def _retry_after_seconds(
        row: tuple[Any, ...] | None, rate_window_seconds: int
    ) -> int:
        """Seconds until the oldest retained failure leaves the quota window."""
        oldest = cast(datetime | None, row[1] if row else None)
        if oldest is None:
            return rate_window_seconds
        oldest_at = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
        remaining = rate_window_seconds - (datetime.now(UTC) - oldest_at).total_seconds()
        return max(1, math.ceil(remaining))

    def resolve_actor(self, raw_token: str) -> Actor | None:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT s.user_id, u.role, u.status "
                "FROM veetee_sessions s "
                "JOIN veetee_users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            return Actor(
                user_id=cast(uuid.UUID, row[0]),
                role=cast(str, row[1]),
                status=cast(str, row[2]),
            )

    def resolve_token(self, raw_token: str) -> uuid.UUID | None:
        actor = self.resolve_actor(raw_token)
        return actor.user_id if actor else None

    def revoke_token(self, raw_token: str) -> bool:
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        with self.database.connection() as connection:
            result = connection.execute(
                "UPDATE veetee_sessions SET revoked_at = now() "
                "WHERE token_hash = %s AND revoked_at IS NULL",
                (token_hash,),
            )
            return bool(result.rowcount > 0)


def record_audit(
    database: PostgresDatabase,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
    *,
    connection: psycopg.Connection[tuple[object, ...]] | None = None,
) -> None:
    """Writes redacted control-plane metadata; callers never pass credentials."""
    def write(conn: psycopg.Connection[tuple[object, ...]]) -> None:
        conn.execute(
            "INSERT INTO veetee_audit_events "
            "(id, actor_user_id, action, resource_type, resource_id, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), actor_user_id, action, resource_type, resource_id,
             Jsonb(metadata or {})),
        )

    if connection is not None:
        write(connection)
        return
    with database.connection() as owned_connection:
        write(owned_connection)


class AgentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list(self, owner_user_id: uuid.UUID) -> list[StoredAgent]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT {AGENT_SELECT_COLUMNS} "
                "FROM veetee_agents WHERE owner_user_id = %s ORDER BY created_at, id",
                (owner_user_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> StoredAgent | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"SELECT {AGENT_SELECT_COLUMNS} "
                "FROM veetee_agents WHERE owner_user_id = %s AND id = %s",
                (owner_user_id, agent_id),
            ).fetchone()
        return self._row(row) if row else None

    def snapshot(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> Any:
        from veetee_server.agents import snapshot_from_agent

        agent = self.get(owner_user_id, agent_id)
        return snapshot_from_agent(agent) if agent is not None else None

    def create(
        self, owner_user_id: uuid.UUID, data: dict[str, Any]
    ) -> tuple[StoredAgent | None, str | None]:
        agent_id = uuid.uuid4()
        with self.database.connection() as connection:
            try:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO veetee_agents (id, owner_user_id, name, role_prompt, "
                        "personality, address_style, language, detail_level, response_style, "
                        "model_id, voice_id, intent_strategy, memory_enabled, "
                        "memory_min_confidence, tool_policy, memory_policy) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (agent_id, owner_user_id, data["name"], data["role_prompt"],
                         data["personality"], data["address_style"], data["language"],
                         data["detail_level"], data["response_style"], data["model_id"],
                         data["voice_id"], data["intent_strategy"], data["memory_enabled"],
                         data["memory_min_confidence"], Jsonb(data["tool_policy"]),
                         Jsonb(data["memory_policy"])),
                    )
            except psycopg.errors.UniqueViolation:
                return None, "duplicate_name"
        stored = self.get(owner_user_id, agent_id)
        if stored is None:
            raise RuntimeError("Created agent row was not persisted")
        return stored, None

    def update(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        expected_version: int,
        data: dict[str, Any],
    ) -> tuple[StoredAgent | None, str | None]:
        fields = AGENT_CONFIG_FIELDS
        values = [
            Jsonb(data[field]) if field in {"tool_policy", "memory_policy"} else data[field]
            for field in fields
        ]
        with self.database.connection() as connection:
            try:
                with connection.transaction():
                    result = connection.execute(
                        "UPDATE veetee_agents SET "
                        + agent_set_clause(fields)
                        + ", version = version + 1, updated_at = now() "
                        "WHERE id = %s AND owner_user_id = %s AND version = %s",
                        (*values, agent_id, owner_user_id, expected_version),
                    )
            except psycopg.errors.UniqueViolation:
                # Rename collides with another agent owned by the same tenant;
                # the savepoint keeps the outer transaction usable.
                return None, "duplicate_name"
            if result.rowcount != 1:
                return None, "changed_or_missing"
            updated = self.get(owner_user_id, agent_id)
            if updated is not None:
                return updated, None
        # The rowcount matched but the follow-up read vanished: treat as conflict.
        return None, "changed_or_missing"

    def delete(self, owner_user_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
        with self.database.connection() as connection:
            result = connection.execute(
                "DELETE FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            )
        return result.rowcount == 1

    @staticmethod
    def _row(row: tuple[Any, ...]) -> StoredAgent:
        profile = dict(zip(AGENT_PROFILE_FIELDS, row[4:], strict=True))
        return StoredAgent(row[0], row[1], row[2], row[3], profile)


@dataclass(frozen=True, slots=True)
class StoredDevice:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID | None
    device_id: str
    alias: str
    board: str
    chip: str
    partition_name: str
    firmware_version: str
    client_id: str
    # Per-device transcript consent policy (M6.2). Tenant-scoped, default off:
    # ``consent_version`` must be non-empty while consent is enabled and the
    # integer ``consent_policy_version`` is the optimistic concurrency token.
    transcript_consent: bool
    consent_version: str
    consent_policy_version: int
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self, is_online: bool = False) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "owner_user_id": str(self.owner_user_id),
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "device_id": self.device_id,
            "alias": self.alias,
            "board": self.board,
            "chip": self.chip,
            "partition": self.partition_name,
            "version": self.firmware_version,
            "client_id": self.client_id,
            "transcript_consent": self.transcript_consent,
            "consent_version": self.consent_version,
            "consent_policy_version": self.consent_policy_version,
            "online": is_online,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class StoredActivation:
    id: uuid.UUID
    device_id: str
    code: str
    challenge: str
    client_id: str
    board: str
    chip: str
    partition_name: str
    firmware_version: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredFirmwareRelease:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    artifact_id: uuid.UUID
    version: str
    board: str
    chip: str
    partition_name: str
    force: bool
    published_at: datetime | None
    created_at: datetime
    storage_name: str
    file_size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "artifact_id": str(self.artifact_id),
            "version": self.version,
            "board": self.board,
            "chip": self.chip,
            "partition": self.partition_name,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "force": self.force,
            "published": self.published_at is not None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DeviceRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_by_device_id(self, device_id: str) -> StoredDevice | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE device_id = %s",
                (device_id,),
            ).fetchone()
            return StoredDevice(*cast(tuple[Any, ...], row)) if row else None

    def get_by_device_and_client_id(self, device_id: str, client_id: str) -> StoredDevice | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE device_id = %s AND client_id = %s",
                (device_id, client_id),
            ).fetchone()
            return StoredDevice(*cast(tuple[Any, ...], row)) if row else None

    def get(self, owner_user_id: uuid.UUID, device_pk: uuid.UUID) -> StoredDevice | None:
        """Returns a tenant-owned device by primary key or None."""
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE id = %s AND owner_user_id = %s",
                (device_pk, owner_user_id),
            ).fetchone()
            return StoredDevice(*cast(tuple[Any, ...], row)) if row else None

    def list(self, owner_user_id: uuid.UUID) -> list[StoredDevice]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE owner_user_id = %s ORDER BY created_at, id",
                (owner_user_id,),
            ).fetchall()
            return [StoredDevice(*cast(tuple[Any, ...], row)) for row in rows]

    def record_check(
        self,
        device_id: str,
        board: str,
        chip: str,
        partition_name: str,
        firmware_version: str,
    ) -> StoredDevice | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "UPDATE veetee_devices SET "
                "board = CASE WHEN %s <> '' THEN %s ELSE board END, "
                "chip = CASE WHEN %s <> '' THEN %s ELSE chip END, "
                "partition_name = CASE WHEN %s <> '' THEN %s ELSE partition_name END, "
                "firmware_version = CASE WHEN %s <> '' THEN %s ELSE firmware_version END, "
                "last_seen_at = now(), updated_at = now() WHERE device_id = %s "
                f"RETURNING {DEVICE_SELECT_COLUMNS}",
                (
                    board, board, chip, chip, partition_name, partition_name,
                    firmware_version, firmware_version, device_id,
                ),
            ).fetchone()
            return StoredDevice(*cast(tuple[Any, ...], row)) if row else None

    def delete(self, owner_user_id: uuid.UUID, device_pk: uuid.UUID) -> StoredDevice | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"DELETE FROM veetee_devices WHERE id = %s AND owner_user_id = %s "
                f"RETURNING {DEVICE_SELECT_COLUMNS}",
                (device_pk, owner_user_id),
            ).fetchone()
            if not row:
                return None
            device = StoredDevice(*cast(tuple[Any, ...], row))
            record_audit(
                self.database,
                owner_user_id,
                "device.unbind",
                "device",
                str(device.id),
                connection=conn,
            )
            conn.execute(
                "DELETE FROM veetee_device_bind_receipts WHERE device_id = %s",
                (device.id,),
            )
            return device

    def set_transcript_consent(
        self,
        owner_user_id: uuid.UUID,
        device_pk: uuid.UUID,
        *,
        enabled: bool,
        consent_version: str,
        expected_policy_version: int,
    ) -> tuple[StoredDevice | None, str | None]:
        """Applies the tenant-owned transcript consent decision for one device.

        The update is optimistic: it only lands when ``consent_policy_version``
        still equals ``expected_policy_version``, so a concurrent change makes
        the caller retry on the fresh state. Enabling requires a non-empty
        version string; disabling clears the stored version. Error codes:
        ``not_found`` (device absent or owned by another tenant),
        ``stale_version`` and ``consent_version_required``. Audit metadata
        carries identifiers and policy versions only, never transcript text.
        """
        if enabled and not consent_version.strip():
            return None, "consent_version_required"
        stored_version = consent_version.strip() if enabled else ""
        with self.database.connection() as conn:
            row = conn.execute(
                f"UPDATE veetee_devices SET "
                f"transcript_consent = %s, consent_version = %s, "
                f"consent_policy_version = consent_policy_version + 1, "
                f"updated_at = now() "
                f"WHERE id = %s AND owner_user_id = %s "
                f"AND consent_policy_version = %s "
                f"RETURNING {DEVICE_SELECT_COLUMNS}",
                (
                    enabled,
                    stored_version,
                    device_pk,
                    owner_user_id,
                    expected_policy_version,
                ),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM veetee_devices WHERE id = %s AND owner_user_id = %s",
                    (device_pk, owner_user_id),
                ).fetchone()
                return None, ("stale_version" if exists else "not_found")
            device = StoredDevice(*cast(tuple[Any, ...], row))
            record_audit(
                self.database,
                owner_user_id,
                "device.transcript_consent.update",
                "device",
                str(device.id),
                {
                    "enabled": device.transcript_consent,
                    "policy_version": device.consent_policy_version,
                },
                connection=conn,
            )
            return device, None


class ActivationRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_or_create(
        self,
        device_id: str,
        client_id: str = "",
        board: str = "",
        chip: str = "",
        partition_name: str = "app",
        firmware_version: str = "",
        ttl_seconds: int = 600,
    ) -> StoredActivation:
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (device_id,))
            conn.execute(
                "DELETE FROM veetee_device_bind_receipts WHERE expires_at <= now()"
            )
            conn.execute(
                "DELETE FROM veetee_device_activations "
                "WHERE device_id = %s AND expires_at <= now()",
                (device_id,),
            )

            row = conn.execute(
                "SELECT id, device_id, code, challenge, client_id, board, chip, partition_name, "
                "firmware_version, expires_at, created_at, updated_at "
                "FROM veetee_device_activations WHERE device_id = %s AND expires_at > now()",
                (device_id,),
            ).fetchone()
            if row:
                return StoredActivation(*cast(tuple[Any, ...], row))

            act_id = uuid.uuid4()
            challenge = secrets.token_urlsafe(24)
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            for _ in range(32):
                code = f"{secrets.randbelow(1_000_000):06d}"
                code_hash = hashlib.sha256(code.encode()).hexdigest()
                try:
                    with conn.transaction():
                        conn.execute(
                            "SELECT pg_advisory_xact_lock(hashtext(%s))", (code,)
                        )
                        conn.execute(
                            "DELETE FROM veetee_device_bind_receipts "
                            "WHERE code_hash = %s AND expires_at <= now()",
                            (code_hash,),
                        )
                        if conn.execute(
                            "SELECT 1 FROM veetee_device_bind_receipts "
                            "WHERE code_hash = %s AND expires_at > now()",
                            (code_hash,),
                        ).fetchone():
                            continue
                        conn.execute(
                            "INSERT INTO veetee_device_activations "
                            "(id, device_id, code, challenge, client_id, board, chip, "
                            "partition_name, firmware_version, expires_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (act_id, device_id, code, challenge, client_id, board, chip,
                             partition_name, firmware_version, expires_at),
                        )
                    break
                except psycopg.errors.UniqueViolation:
                    continue
            else:
                raise RuntimeError("Unable to allocate activation code")

            row = conn.execute(
                "SELECT id, device_id, code, challenge, client_id, board, chip, partition_name, "
                "firmware_version, expires_at, created_at, updated_at "
                "FROM veetee_device_activations WHERE id = %s",
                (act_id,),
            ).fetchone()
            return StoredActivation(*cast(tuple[Any, ...], row))

    def bind_device(
        self,
        owner_user_id: uuid.UUID,
        agent_id: uuid.UUID,
        code: str,
        rate_limit: int = 20,
        rate_window_seconds: int = 600,
        receipt_ttl_seconds: int = 600,
    ) -> tuple[StoredDevice | None, str | None]:
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (code,))
            conn.execute(
                "DELETE FROM veetee_device_bind_receipts WHERE expires_at <= now()"
            )

            agent_exists = conn.execute(
                "SELECT 1 FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            if not agent_exists:
                return None, "agent_not_found"

            code_hash = hashlib.sha256(code.encode()).hexdigest()
            receipt_row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} FROM veetee_devices WHERE id = ("
                "SELECT device_id FROM veetee_device_bind_receipts "
                "WHERE code_hash = %s AND expires_at > now() FOR UPDATE) FOR UPDATE",
                (code_hash,),
            ).fetchone()
            if receipt_row:
                receipt_device = StoredDevice(*cast(tuple[Any, ...], receipt_row))
                if (
                    receipt_device.owner_user_id == owner_user_id
                    and receipt_device.agent_id == agent_id
                ):
                    return receipt_device, None
                return None, "already_bound_conflict"

            attempt_row = conn.execute(
                "SELECT count(*) FROM veetee_device_bind_attempts "
                "WHERE owner_user_id = %s AND attempted_at > now() - (%s * interval '1 second')",
                (owner_user_id, rate_window_seconds),
            ).fetchone()
            attempt_count = cast(int, attempt_row[0]) if attempt_row else 0
            if attempt_count >= rate_limit:
                return None, "rate_limited"
            conn.execute(
                "INSERT INTO veetee_device_bind_attempts(owner_user_id) VALUES (%s)",
                (owner_user_id,),
            )

            row = conn.execute(
                "SELECT id, device_id, code, challenge, client_id, board, chip, partition_name, "
                "firmware_version, expires_at, created_at, updated_at "
                "FROM veetee_device_activations WHERE code = %s FOR UPDATE",
                (code,),
            ).fetchone()
            if not row:
                return None, "invalid_code"

            act = StoredActivation(*cast(tuple[Any, ...], row))
            now_time = datetime.now(UTC)
            expires_at = (
                act.expires_at
                if act.expires_at.tzinfo
                else act.expires_at.replace(tzinfo=UTC)
            )

            if expires_at <= now_time:
                return None, "expired_code"

            existing_row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE device_id = %s",
                (act.device_id,),
            ).fetchone()

            if existing_row:
                existing_dev = StoredDevice(*cast(tuple[Any, ...], existing_row))
                if (
                    existing_dev.owner_user_id == owner_user_id
                    and existing_dev.agent_id == agent_id
                ):
                    return existing_dev, None
                return None, "already_bound_conflict"

            dev_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO veetee_devices "
                "(id, owner_user_id, agent_id, device_id, board, chip, partition_name, "
                "firmware_version, client_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (dev_id, owner_user_id, agent_id, act.device_id, act.board, act.chip,
                 act.partition_name, act.firmware_version, act.client_id),
            )

            conn.execute("DELETE FROM veetee_device_activations WHERE id = %s", (act.id,))
            conn.execute(
                "INSERT INTO veetee_device_bind_receipts "
                "(id, code_hash, device_id, owner_user_id, agent_id, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, now() + (%s * interval '1 second'))",
                (uuid.uuid4(), hashlib.sha256(code.encode()).hexdigest(), dev_id,
                 owner_user_id, agent_id, receipt_ttl_seconds),
            )

            record_audit(
                self.database,
                owner_user_id,
                "device.bind",
                "device",
                str(dev_id),
                {"agent_id": str(agent_id)},
                connection=conn,
            )

            dev_row = conn.execute(
                f"SELECT {DEVICE_SELECT_COLUMNS} "
                "FROM veetee_devices WHERE id = %s",
                (dev_id,),
            ).fetchone()
            if dev_row is None:
                raise RuntimeError("Bound device row was not persisted")
            return StoredDevice(*cast(tuple[Any, ...], dev_row)), None


class FirmwareReleaseRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create_artifact(
        self,
        owner_user_id: uuid.UUID,
        storage_name: str,
        file_size: int,
        sha256: str,
    ) -> uuid.UUID:
        artifact_id = uuid.uuid4()
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO veetee_firmware_artifacts "
                "(id, owner_user_id, storage_name, file_size, sha256) VALUES (%s, %s, %s, %s, %s)",
                (artifact_id, owner_user_id, storage_name, file_size, sha256),
            )
            record_audit(
                self.database, owner_user_id, "ota.artifact.upload", "firmware_artifact",
                str(artifact_id), {"size": file_size, "sha256": sha256}, connection=conn,
            )
        return artifact_id

    def create_release(
        self,
        owner_user_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version: str,
        board: str,
        chip: str,
        partition_name: str,
        force: bool,
    ) -> StoredFirmwareRelease:
        rel_id = uuid.uuid4()
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO veetee_firmware_releases "
                "(id, owner_user_id, artifact_id, version, board, chip, partition_name, force) "
                "SELECT %s, %s, id, %s, %s, %s, %s, %s FROM veetee_firmware_artifacts "
                "WHERE id = %s AND owner_user_id = %s",
                (rel_id, owner_user_id, version, board, chip, partition_name, force,
                 artifact_id, owner_user_id),
            )
            exists = conn.execute(
                "SELECT 1 FROM veetee_firmware_releases WHERE id = %s", (rel_id,)
            ).fetchone()
            if exists is None:
                raise LookupError("Artifact not found")
        return cast(StoredFirmwareRelease, self.get(rel_id))

    def publish(
        self, owner_user_id: uuid.UUID, release_id: uuid.UUID
    ) -> StoredFirmwareRelease | None:
        with self.database.connection() as conn:
            result = conn.execute(
                "UPDATE veetee_firmware_releases SET published_at = COALESCE(published_at, now()) "
                "WHERE id = %s AND owner_user_id = %s",
                (release_id, owner_user_id),
            )
            if result.rowcount != 1:
                return None
            record_audit(
                self.database, owner_user_id, "ota.release.publish", "firmware_release",
                str(release_id), connection=conn,
            )
        return self.get(release_id)

    def get(self, release_id: uuid.UUID) -> StoredFirmwareRelease | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT r.id, r.owner_user_id, r.artifact_id, r.version, r.board, r.chip, "
                "r.partition_name, r.force, r.published_at, r.created_at, a.storage_name, "
                "a.file_size, a.sha256 FROM veetee_firmware_releases r "
                "JOIN veetee_firmware_artifacts a ON a.id = r.artifact_id WHERE r.id = %s",
                (release_id,),
            ).fetchone()
            return StoredFirmwareRelease(*cast(tuple[Any, ...], row)) if row else None

    def get_by_artifact(self, artifact_id: uuid.UUID) -> StoredFirmwareRelease | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT r.id, r.owner_user_id, r.artifact_id, r.version, r.board, r.chip, "
                "r.partition_name, r.force, r.published_at, r.created_at, a.storage_name, "
                "a.file_size, a.sha256 FROM veetee_firmware_releases r "
                "JOIN veetee_firmware_artifacts a ON a.id = r.artifact_id "
                "WHERE r.artifact_id = %s AND r.published_at IS NOT NULL",
                (artifact_id,),
            ).fetchone()
            return StoredFirmwareRelease(*cast(tuple[Any, ...], row)) if row else None

    def find_eligible(
        self,
        owner_user_id: uuid.UUID,
        board: str,
        chip: str,
        partition_name: str,
        current_version: str,
    ) -> StoredFirmwareRelease | None:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT r.id, r.owner_user_id, r.artifact_id, r.version, r.board, r.chip, "
                "r.partition_name, r.force, r.published_at, r.created_at, a.storage_name, "
                "a.file_size, a.sha256 FROM veetee_firmware_releases r "
                "JOIN veetee_firmware_artifacts a ON a.id = r.artifact_id "
                "WHERE r.published_at IS NOT NULL AND r.owner_user_id = %s "
                "AND r.board = %s AND r.chip = %s "
                "AND r.partition_name = %s",
                (owner_user_id, board, chip, partition_name),
            ).fetchall()
            releases = [
                StoredFirmwareRelease(*cast(tuple[Any, ...], row)) for row in rows
            ]

        eligible: list[tuple[tuple[int, ...], StoredFirmwareRelease]] = []
        for r in releases:
            parsed_v = parse_semver(r.version)
            parsed_curr = parse_semver(current_version)

            if r.force or parsed_v > parsed_curr:
                eligible.append((parsed_v, r))

        if not eligible:
            return None

        eligible.sort(key=lambda x: x[0], reverse=True)
        return eligible[0][1]


@dataclass(frozen=True, slots=True)
class StoredProvider:
    id: uuid.UUID
    provider_kind: str
    provider_id: str
    enabled: bool
    is_default: bool
    version: int
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.provider_kind,
            "provider_id": self.provider_id,
            "enabled": self.enabled,
            "default": self.is_default,
            "is_default": self.is_default,
            "version": self.version,
            "updated_by": str(self.updated_by) if self.updated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProviderRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list(self) -> list[StoredProvider]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT id, provider_kind, provider_id, enabled, is_default, version, "
                "updated_by, created_at, updated_at "
                "FROM veetee_providers ORDER BY provider_kind, provider_id"
            ).fetchall()
            return [StoredProvider(*cast(tuple[Any, ...], row)) for row in rows]

    def get(self, provider_kind: str, provider_id: str) -> StoredProvider | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT id, provider_kind, provider_id, enabled, is_default, version, "
                "updated_by, created_at, updated_at "
                "FROM veetee_providers WHERE provider_kind = %s AND provider_id = %s",
                (provider_kind, provider_id),
            ).fetchone()
            return StoredProvider(*cast(tuple[Any, ...], row)) if row else None

    def is_provider_enabled(self, provider_kind: str, provider_id: str) -> bool:
        stored = self.get(provider_kind, provider_id)
        if stored is None:
            return True
        return stored.enabled

    def update_state(
        self,
        actor_user_id: uuid.UUID,
        provider_kind: str,
        provider_id: str,
        expected_version: int,
        enabled: bool | None = None,
        is_default: bool | None = None,
    ) -> tuple[StoredProvider | None, str | None]:
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (provider_kind,))

            current = self._get_with_conn(conn, provider_kind, provider_id)
            if current is None:
                return None, "not_found"

            if current.version != expected_version:
                return None, "conflict"

            new_enabled = current.enabled if enabled is None else enabled
            new_is_default = current.is_default if is_default is None else is_default

            if new_is_default and not new_enabled:
                return None, "default_must_be_enabled"

            if not new_enabled and current.is_default:
                return None, "cannot_disable_default"

            if not new_is_default and current.is_default:
                return None, "cannot_unset_default"

            try:
                with conn.transaction():
                    if new_is_default and not current.is_default:
                        conn.execute(
                            "UPDATE veetee_providers SET is_default = false, "
                            "version = version + 1, updated_at = now() "
                            "WHERE provider_kind = %s AND is_default = true",
                            (provider_kind,),
                        )

                    res = conn.execute(
                        "UPDATE veetee_providers SET "
                        "enabled = %s, is_default = %s, version = version + 1, "
                        "updated_by = %s, updated_at = now() "
                        "WHERE provider_kind = %s AND provider_id = %s AND version = %s "
                        "RETURNING id, provider_kind, provider_id, enabled, is_default, version, "
                        "updated_by, created_at, updated_at",
                        (
                            new_enabled,
                            new_is_default,
                            actor_user_id,
                            provider_kind,
                            provider_id,
                            expected_version,
                        ),
                    )
                    row = res.fetchone()
                    if row is None:
                        return None, "conflict"

                    updated_provider = StoredProvider(*cast(tuple[Any, ...], row))
                    record_audit(
                        self.database,
                        actor_user_id,
                        "provider.update",
                        "provider",
                        f"{provider_kind}:{provider_id}",
                        {
                            "enabled": updated_provider.enabled,
                            "is_default": updated_provider.is_default,
                            "version": updated_provider.version,
                        },
                        connection=conn,
                    )
                    return updated_provider, None

            except psycopg.errors.CheckViolation:
                return None, "constraint_violation"
            except psycopg.errors.UniqueViolation:
                return None, "unique_violation"

    def _get_with_conn(
        self, conn: psycopg.Connection[tuple[object, ...]], provider_kind: str, provider_id: str
    ) -> StoredProvider | None:
        row = conn.execute(
            "SELECT id, provider_kind, provider_id, enabled, is_default, version, "
            "updated_by, created_at, updated_at "
            "FROM veetee_providers WHERE provider_kind = %s AND provider_id = %s",
            (provider_kind, provider_id),
        ).fetchone()
        return StoredProvider(*cast(tuple[Any, ...], row)) if row else None
