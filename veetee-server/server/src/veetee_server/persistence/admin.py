"""M6.8 Administration & Quota Governance persistence layer.

Provides repositories and services for user management, reset tokens,
versioned typed system settings, audit search, and quota policies/buckets.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import psycopg
from psycopg.types.json import Jsonb

from .database import PostgresDatabase
from .repository import hash_login_identifier, hash_password, record_audit

logger = logging.getLogger("veetee.persistence.admin")

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class QuotaServiceError(Exception):
    """Raised when quota enforcement fails closed due to persistence error."""


@dataclass(frozen=True, slots=True)
class StoredUser:
    id: uuid.UUID
    email: str
    role: str
    status: str
    version: int
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "email": self.email,
            "role": self.role,
            "status": self.status,
            "version": self.version,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class StoredSetting:
    key: str
    value: Any
    version: int
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "version": self.version,
            "updated_by": str(self.updated_by) if self.updated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class StoredUserQuota:
    user_id: uuid.UUID
    llm_tokens_per_day: int | None
    tts_chars_per_day: int | None
    tool_calls_per_minute: int | None
    rag_bytes_per_month: int | None
    enabled: bool
    version: int
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "llm_tokens_per_day": self.llm_tokens_per_day,
            "tts_chars_per_day": self.tts_chars_per_day,
            "tool_calls_per_minute": self.tool_calls_per_minute,
            "rag_bytes_per_month": self.rag_bytes_per_month,
            "enabled": self.enabled,
            "version": self.version,
            "updated_by": str(self.updated_by) if self.updated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True, slots=True)
class QuotaCheckResult:
    allowed: bool
    limit: int | None
    current_usage: int
    remaining: int | None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "limit": self.limit,
            "current_usage": self.current_usage,
            "remaining": self.remaining,
            "reason": self.reason,
        }


class AdminUserRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list_users(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        role: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[StoredUser], int]:
        page = max(1, page)
        limit = max(1, min(100, limit))
        offset = (page - 1) * limit

        where_clauses: list[str] = []
        params: list[Any] = []

        if role is not None and role.strip():
            where_clauses.append("role = %s")
            params.append(role.strip())

        if status is not None and status.strip():
            where_clauses.append("status = %s")
            params.append(status.strip())

        if search is not None and search.strip():
            where_clauses.append("email ILIKE %s")
            params.append(f"%{search.strip()}%")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self.database.connection() as conn:
            count_row = conn.execute(
                f"SELECT count(*) FROM veetee_users {where_sql}", params
            ).fetchone()
            total = cast(int, count_row[0]) if count_row else 0

            rows = conn.execute(
                f"SELECT id, email, role, status, version, last_login_at, created_at, updated_at "
                f"FROM veetee_users {where_sql} ORDER BY created_at DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (*params, limit, offset),
            ).fetchall()

            users = [StoredUser(*cast(tuple[Any, ...], row)) for row in rows]
            return users, total

    def create_user(
        self,
        creator_id: uuid.UUID | None,
        email: str,
        role: str,
        status: str = "active",
        token_ttl_seconds: int = 86400,
    ) -> tuple[StoredUser | None, str | None, str | None]:
        email_clean = email.strip().lower()
        if not EMAIL_REGEX.match(email_clean):
            return None, None, "invalid_email"
        if role not in ("owner", "admin"):
            return None, None, "invalid_role"
        if status not in ("active", "suspended"):
            return None, None, "invalid_status"

        user_id = uuid.uuid4()
        dummy_pass = secrets.token_urlsafe(32)
        pwd_hash = hash_password(dummy_pass)

        raw_reset_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_reset_token.encode()).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(seconds=token_ttl_seconds)

        with self.database.connection() as conn:
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO veetee_users "
                        "(id, email, password_hash, role, status, version) "
                        "VALUES (%s, %s, %s, %s, %s, 1)",
                        (user_id, email_clean, pwd_hash, role, status),
                    )
                    conn.execute(
                        "INSERT INTO veetee_password_reset_tokens "
                        "(id, user_id, token_hash, expires_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (uuid.uuid4(), user_id, token_hash, expires_at, creator_id),
                    )
                    email_hash = hash_login_identifier(email_clean)
                    record_audit(
                        self.database,
                        creator_id,
                        "admin.user.create",
                        "user",
                        str(user_id),
                        {"role": role, "status": status, "email_hash": email_hash},
                        connection=conn,
                    )
            except psycopg.errors.UniqueViolation:
                return None, None, "duplicate_email"

            user_row = conn.execute(
                "SELECT id, email, role, status, version, last_login_at, created_at, updated_at "
                "FROM veetee_users WHERE id = %s",
                (user_id,),
            ).fetchone()
            if user_row is None:
                return None, None, "create_failed"
            return StoredUser(*cast(tuple[Any, ...], user_row)), raw_reset_token, None

    def update_user(
        self,
        actor_id: uuid.UUID,
        target_user_id: uuid.UUID,
        expected_version: int,
        role: str | None = None,
        status: str | None = None,
    ) -> tuple[StoredUser | None, str | None]:
        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('veetee_users'))")
            current_row = conn.execute(
                "SELECT id, email, role, status, version, last_login_at, created_at, updated_at "
                "FROM veetee_users WHERE id = %s",
                (target_user_id,),
            ).fetchone()
            if current_row is None:
                return None, "not_found"

            curr_user = StoredUser(*cast(tuple[Any, ...], current_row))

            if curr_user.version != expected_version:
                return None, "version_conflict"

            new_role = curr_user.role if role is None else role
            new_status = curr_user.status if status is None else status

            if new_role not in ("owner", "admin"):
                return None, "invalid_role"
            if new_status not in ("active", "suspended"):
                return None, "invalid_status"

            if actor_id == target_user_id and (new_status == "suspended" or new_role != "admin"):
                return None, "cannot_suspend_self"

            if curr_user.role == "admin" and (new_status == "suspended" or new_role != "admin"):
                active_admins = conn.execute(
                    "SELECT count(*) FROM veetee_users "
                    "WHERE role = 'admin' AND status = 'active' AND id <> %s",
                    (target_user_id,),
                ).fetchone()
                remaining = cast(int, active_admins[0]) if active_admins else 0
                if remaining == 0:
                    return None, "last_admin_lockout"

            with conn.transaction():
                res = conn.execute(
                    "UPDATE veetee_users SET role = %s, status = %s, "
                    "version = version + 1, updated_at = now() "
                    "WHERE id = %s AND version = %s "
                    "RETURNING id, email, role, status, version, last_login_at, "
                    "created_at, updated_at",
                    (new_role, new_status, target_user_id, expected_version),
                )
                row = res.fetchone()
                if row is None:
                    return None, "version_conflict"

                if new_status == "suspended":
                    conn.execute(
                        "UPDATE veetee_sessions SET revoked_at = now() "
                        "WHERE user_id = %s AND revoked_at IS NULL",
                        (target_user_id,),
                    )

                record_audit(
                    self.database,
                    actor_id,
                    "admin.user.update",
                    "user",
                    str(target_user_id),
                    {
                        "old_role": curr_user.role,
                        "new_role": new_role,
                        "old_status": curr_user.status,
                        "new_status": new_status,
                    },
                    connection=conn,
                )
                return StoredUser(*cast(tuple[Any, ...], row)), None

    def issue_reset_token(
        self,
        creator_id: uuid.UUID | None,
        target_user_id: uuid.UUID,
        ttl_seconds: int = 86400,
    ) -> tuple[str | None, str | None]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

        with self.database.connection() as conn:
            user_exists = conn.execute(
                "SELECT 1 FROM veetee_users WHERE id = %s", (target_user_id,)
            ).fetchone()
            if not user_exists:
                return None, "user_not_found"

            with conn.transaction():
                conn.execute(
                    "UPDATE veetee_password_reset_tokens SET used_at = now() "
                    "WHERE user_id = %s AND used_at IS NULL",
                    (target_user_id,),
                )
                conn.execute(
                    "INSERT INTO veetee_password_reset_tokens "
                    "(id, user_id, token_hash, expires_at, created_by) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (uuid.uuid4(), target_user_id, token_hash, expires_at, creator_id),
                )
                record_audit(
                    self.database,
                    creator_id,
                    "admin.user.reset_token_issued",
                    "user",
                    str(target_user_id),
                    connection=conn,
                )
            return raw_token, None

    def consume_reset_token(
        self,
        raw_token: str,
        new_password: str,
    ) -> tuple[bool, str | None]:
        if len(new_password) < 12:
            return False, "password_too_short"

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        pwd_hash = hash_password(new_password)

        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (token_hash,))
            token_row = conn.execute(
                "SELECT id, user_id, expires_at, used_at FROM veetee_password_reset_tokens "
                "WHERE token_hash = %s",
                (token_hash,),
            ).fetchone()

            if token_row is None:
                return False, "invalid_token"

            tok_id = cast(uuid.UUID, token_row[0])
            user_id = cast(uuid.UUID, token_row[1])
            expires_at = cast(datetime, token_row[2])
            used_at = cast(datetime | None, token_row[3])
            now_time = datetime.now(UTC)
            exp_time = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)

            if used_at is not None or exp_time <= now_time:
                return False, "expired_or_used_token"

            with conn.transaction():
                conn.execute(
                    "UPDATE veetee_password_reset_tokens SET used_at = now() WHERE id = %s",
                    (tok_id,),
                )
                conn.execute(
                    "UPDATE veetee_users SET password_hash = %s, "
                    "version = version + 1, updated_at = now() "
                    "WHERE id = %s",
                    (pwd_hash, user_id),
                )
                conn.execute(
                    "UPDATE veetee_sessions SET revoked_at = now() "
                    "WHERE user_id = %s AND revoked_at IS NULL",
                    (user_id,),
                )
                record_audit(
                    self.database,
                    user_id,
                    "auth.password_reset.success",
                    "user",
                    str(user_id),
                    connection=conn,
                )
            return True, None


ALLOWED_SETTINGS_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "conversation_retention_days": int,
    "quota_enabled": bool,
    "default_quota_llm_tokens_per_day": (int, type(None)),
    "default_quota_tts_chars_per_day": (int, type(None)),
    "default_quota_tool_calls_per_minute": (int, type(None)),
    "default_quota_rag_bytes_per_month": (int, type(None)),
}


class SystemSettingsRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def list_settings(self) -> list[StoredSetting]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT key, value_json, version, updated_by, created_at, updated_at "
                "FROM veetee_system_settings WHERE key = ANY(%s) ORDER BY key",
                (list(ALLOWED_SETTINGS_SCHEMA.keys()),),
            ).fetchall()
            return [StoredSetting(*cast(tuple[Any, ...], row)) for row in rows]

    def get_setting(self, key: str) -> StoredSetting | None:
        if key not in ALLOWED_SETTINGS_SCHEMA:
            return None
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT key, value_json, version, updated_by, created_at, updated_at "
                "FROM veetee_system_settings WHERE key = %s",
                (key,),
            ).fetchone()
            if row:
                return StoredSetting(*cast(tuple[Any, ...], row))
            return None

    def update_setting(
        self,
        actor_id: uuid.UUID,
        key: str,
        value: Any,
        expected_version: int,
    ) -> tuple[StoredSetting | None, str | None]:
        if key not in ALLOWED_SETTINGS_SCHEMA:
            return None, "invalid_key"

        expected_type = ALLOWED_SETTINGS_SCHEMA[key]
        if isinstance(expected_type, tuple):
            if not isinstance(value, expected_type) or isinstance(value, bool):
                return None, "invalid_value_type"
        else:
            if not isinstance(value, expected_type) or isinstance(value, bool) != (
                expected_type is bool
            ):
                return None, "invalid_value_type"

        if isinstance(value, int) and value < 0:
            return None, "negative_value"
        if (
            key == "conversation_retention_days"
            and isinstance(value, int)
            and value < 1
        ):
            return None, "negative_value"

        with self.database.connection() as conn:
            with conn.transaction():
                res = conn.execute(
                    "INSERT INTO veetee_system_settings (key, value_json, version, updated_by) "
                    "VALUES (%s, %s, 1, %s) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "value_json = EXCLUDED.value_json, "
                    "version = veetee_system_settings.version + 1, "
                    "updated_by = EXCLUDED.updated_by, updated_at = now() "
                    "WHERE veetee_system_settings.version = %s "
                    "RETURNING key, value_json, version, updated_by, created_at, updated_at",
                    (key, Jsonb(value), actor_id, expected_version),
                )
                row = res.fetchone()
                if row is None:
                    return None, "version_conflict"
                setting = StoredSetting(*cast(tuple[Any, ...], row))
                record_audit(
                    self.database,
                    actor_id,
                    "admin.setting.update",
                    "setting",
                    key,
                    {"version": setting.version},
                    connection=conn,
                )
                return setting, None


class AuditLogRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def search(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        action: str | None = None,
        resource_type: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        page = max(1, page)
        limit = max(1, min(100, limit))
        offset = (page - 1) * limit

        where_clauses: list[str] = []
        params: list[Any] = []

        if action is not None and action.strip():
            where_clauses.append("action LIKE %s")
            params.append(f"{action.strip()}%")

        if resource_type is not None and resource_type.strip():
            where_clauses.append("resource_type = %s")
            params.append(resource_type.strip())

        if actor_user_id is not None:
            where_clauses.append("actor_user_id = %s")
            params.append(actor_user_id)

        if start_time is not None:
            where_clauses.append("created_at >= %s")
            params.append(start_time)

        if end_time is not None:
            where_clauses.append("created_at <= %s")
            params.append(end_time)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with self.database.connection() as conn:
            count_row = conn.execute(
                f"SELECT count(*) FROM veetee_audit_events {where_sql}", params
            ).fetchone()
            total = cast(int, count_row[0]) if count_row else 0

            rows = conn.execute(
                "SELECT id, actor_user_id, action, resource_type, resource_id, "
                f"metadata, created_at FROM veetee_audit_events {where_sql} "
                "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                (*params, limit, offset),
            ).fetchall()

            items = [
                {
                    "id": str(r[0]),
                    "actor_user_id": str(r[1]) if r[1] else None,
                    "action": r[2],
                    "resource_type": r[3],
                    "resource_id": r[4],
                    "metadata": r[5] or {},
                    "created_at": cast(datetime, r[6]).isoformat() if r[6] else None,
                }
                for r in rows
            ]
            return items, total


class QuotaRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_user_quota(self, user_id: uuid.UUID) -> StoredUserQuota | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT user_id, llm_tokens_per_day, tts_chars_per_day, tool_calls_per_minute, "
                "rag_bytes_per_month, enabled, version, updated_by, created_at, updated_at "
                "FROM veetee_user_quotas WHERE user_id = %s",
                (user_id,),
            ).fetchone()
            return StoredUserQuota(*cast(tuple[Any, ...], row)) if row else None

    def user_exists(self, user_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM veetee_users WHERE id = %s", (user_id,)
            ).fetchone() is not None

    def update_user_quota(
        self,
        actor_id: uuid.UUID,
        target_user_id: uuid.UUID,
        expected_version: int,
        llm_tokens_per_day: int | None,
        tts_chars_per_day: int | None,
        tool_calls_per_minute: int | None,
        rag_bytes_per_month: int | None,
        enabled: bool,
    ) -> tuple[StoredUserQuota | None, str | None]:
        for val, name in (
            (llm_tokens_per_day, "llm_tokens_per_day"),
            (tts_chars_per_day, "tts_chars_per_day"),
            (tool_calls_per_minute, "tool_calls_per_minute"),
            (rag_bytes_per_month, "rag_bytes_per_month"),
        ):
            if val is not None and val < 0:
                return None, f"invalid_{name}"

        with self.database.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"quota:{target_user_id}",))
            if (
                conn.execute(
                    "SELECT 1 FROM veetee_users WHERE id = %s", (target_user_id,)
                ).fetchone()
                is None
            ):
                return None, "user_not_found"
            current_row = conn.execute(
                "SELECT user_id, llm_tokens_per_day, tts_chars_per_day, "
                "tool_calls_per_minute, rag_bytes_per_month, enabled, version, "
                "updated_by, created_at, updated_at FROM veetee_user_quotas "
                "WHERE user_id = %s",
                (target_user_id,),
            ).fetchone()
            current = (
                StoredUserQuota(*cast(tuple[Any, ...], current_row))
                if current_row is not None
                else None
            )
            if current is None and expected_version != 1:
                return None, "version_conflict"
            if current is not None and current.version != expected_version:
                return None, "version_conflict"

            with conn.transaction():
                res = conn.execute(
                    "INSERT INTO veetee_user_quotas "
                    "(user_id, llm_tokens_per_day, tts_chars_per_day, tool_calls_per_minute, "
                    "rag_bytes_per_month, enabled, version, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 1, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "llm_tokens_per_day = EXCLUDED.llm_tokens_per_day, "
                    "tts_chars_per_day = EXCLUDED.tts_chars_per_day, "
                    "tool_calls_per_minute = EXCLUDED.tool_calls_per_minute, "
                    "rag_bytes_per_month = EXCLUDED.rag_bytes_per_month, "
                    "enabled = EXCLUDED.enabled, "
                    "version = veetee_user_quotas.version + 1, "
                    "updated_by = EXCLUDED.updated_by, updated_at = now() "
                    "WHERE veetee_user_quotas.version = %s "
                    "RETURNING user_id, llm_tokens_per_day, tts_chars_per_day, "
                    "tool_calls_per_minute, "
                    "rag_bytes_per_month, enabled, version, updated_by, created_at, updated_at",
                    (
                        target_user_id,
                        llm_tokens_per_day,
                        tts_chars_per_day,
                        tool_calls_per_minute,
                        rag_bytes_per_month,
                        enabled,
                        actor_id,
                        expected_version if current is not None else 1,
                    ),
                )
                row = res.fetchone()
                if row is None:
                    return None, "version_conflict"
                quota = StoredUserQuota(*cast(tuple[Any, ...], row))
                record_audit(
                    self.database,
                    actor_id,
                    "admin.quota.update",
                    "user_quota",
                    str(target_user_id),
                    {"enabled": enabled, "version": quota.version},
                    connection=conn,
                )
                return quota, None


def get_quota_window_start(metric_type: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    if metric_type in ("llm_tokens_day", "tts_chars_day"):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif metric_type == "tool_calls_minute":
        return now.replace(second=0, microsecond=0)
    elif metric_type == "rag_bytes_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unknown metric_type: {metric_type}")


METRIC_TO_USER_FIELD = {
    "llm_tokens_day": "llm_tokens_per_day",
    "tts_chars_day": "tts_chars_per_day",
    "tool_calls_minute": "tool_calls_per_minute",
    "rag_bytes_month": "rag_bytes_per_month",
}

METRIC_TO_SETTING_KEY = {
    "llm_tokens_day": "default_quota_llm_tokens_per_day",
    "tts_chars_day": "default_quota_tts_chars_per_day",
    "tool_calls_minute": "default_quota_tool_calls_per_minute",
    "rag_bytes_month": "default_quota_rag_bytes_per_month",
}


class QuotaService:
    """Thread-safe & atomic quota service backed by PostgreSQL buckets."""

    def __init__(
        self,
        database: PostgresDatabase,
        settings_repo: SystemSettingsRepository,
        quota_repo: QuotaRepository,
    ) -> None:
        self.database = database
        self.settings_repo = settings_repo
        self.quota_repo = quota_repo

    def is_quota_enabled(self, user_id: uuid.UUID) -> bool:
        """Returns True if quota enforcement is active globally or for the user."""
        try:
            glob_enabled_setting = self.settings_repo.get_setting("quota_enabled")
            glob_enabled = bool(glob_enabled_setting.value) if glob_enabled_setting else False
            user_quota = self.quota_repo.get_user_quota(user_id)
            user_enabled = user_quota.enabled if user_quota else False
            return glob_enabled or user_enabled
        except Exception:
            # If checking enabled status fails, default to enabled for safety or re-raise
            return True

    def get_effective_limit(self, user_id: uuid.UUID, metric_type: str) -> int | None:
        user_quota = self.quota_repo.get_user_quota(user_id)
        field_name = METRIC_TO_USER_FIELD.get(metric_type)
        if user_quota and field_name:
            val = getattr(user_quota, field_name)
            if val is not None:
                return cast(int, val)

        setting_key = METRIC_TO_SETTING_KEY.get(metric_type)
        if setting_key:
            setting = self.settings_repo.get_setting(setting_key)
            if setting and setting.value is not None:
                return cast(int, setting.value)
        return None

    def check_only(
        self, user_id: uuid.UUID, metric_type: str, now: datetime | None = None
    ) -> QuotaCheckResult:
        try:
            if not self.is_quota_enabled(user_id):
                return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

            limit = self.get_effective_limit(user_id, metric_type)
            if limit is None:
                return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

            window_start = get_quota_window_start(metric_type, now)
            with self.database.connection() as conn:
                row = conn.execute(
                    "SELECT used_amount FROM veetee_quota_usage_buckets "
                    "WHERE user_id = %s AND metric_type = %s AND window_start = %s",
                    (user_id, metric_type, window_start),
                ).fetchone()
                current_usage = cast(int, row[0]) if row else 0

            allowed = current_usage < limit
            remaining = max(0, limit - current_usage)
            reason = f"Quota exceeded for {metric_type}" if not allowed else None
            return QuotaCheckResult(
                allowed=allowed,
                limit=limit,
                current_usage=current_usage,
                remaining=remaining,
                reason=reason,
            )
        except Exception as exc:
            if self.is_quota_enabled(user_id):
                logger.error("Quota check failed closed for user %s: %s", user_id, exc)
                raise QuotaServiceError(f"Quota enforcement unavailable: {exc}") from exc
            return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

    def check_and_consume(
        self, user_id: uuid.UUID, metric_type: str, amount: int, now: datetime | None = None
    ) -> QuotaCheckResult:
        if amount < 0:
            raise ValueError("Amount to consume cannot be negative")

        try:
            if not self.is_quota_enabled(user_id):
                return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

            limit = self.get_effective_limit(user_id, metric_type)
            if limit is None:
                # Unlimited, but we still record usage bucket if needed
                if amount > 0:
                    self.record_usage(user_id, metric_type, amount, now)
                return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

            window_start = get_quota_window_start(metric_type, now)
            lock_key = f"{user_id}:{metric_type}"

            with self.database.connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                row = conn.execute(
                    "SELECT used_amount FROM veetee_quota_usage_buckets "
                    "WHERE user_id = %s AND metric_type = %s AND window_start = %s",
                    (user_id, metric_type, window_start),
                ).fetchone()
                current_usage = cast(int, row[0]) if row else 0

                if current_usage + amount > limit:
                    remaining = max(0, limit - current_usage)
                    return QuotaCheckResult(
                        allowed=False,
                        limit=limit,
                        current_usage=current_usage,
                        remaining=remaining,
                        reason=f"Quota exceeded for {metric_type}",
                    )

                res = conn.execute(
                    "INSERT INTO veetee_quota_usage_buckets "
                    "(user_id, metric_type, window_start, used_amount, updated_at) "
                    "VALUES (%s, %s, %s, %s, now()) "
                    "ON CONFLICT (user_id, metric_type, window_start) DO UPDATE SET "
                    "used_amount = veetee_quota_usage_buckets.used_amount "
                    "+ EXCLUDED.used_amount, updated_at = now() "
                    "RETURNING used_amount",
                    (user_id, metric_type, window_start, amount),
                )
                usage_row = res.fetchone()
                if usage_row is None:  # pragma: no cover - RETURNING is guaranteed
                    raise QuotaServiceError("Quota usage update returned no row")
                new_usage = cast(int, usage_row[0])
                remaining = max(0, limit - new_usage)
                return QuotaCheckResult(
                    allowed=True,
                    limit=limit,
                    current_usage=new_usage,
                    remaining=remaining,
                )
        except Exception as exc:
            if self.is_quota_enabled(user_id):
                logger.error("Quota check_and_consume failed closed for user %s: %s", user_id, exc)
                raise QuotaServiceError(f"Quota enforcement unavailable: {exc}") from exc
            return QuotaCheckResult(allowed=True, limit=None, current_usage=0, remaining=None)

    def record_usage(
        self, user_id: uuid.UUID, metric_type: str, amount: int, now: datetime | None = None
    ) -> None:
        if amount <= 0:
            return
        try:
            if not self.is_quota_enabled(user_id):
                return
            window_start = get_quota_window_start(metric_type, now)
            lock_key = f"{user_id}:{metric_type}"
            with self.database.connection() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                conn.execute(
                    "INSERT INTO veetee_quota_usage_buckets "
                    "(user_id, metric_type, window_start, used_amount, updated_at) "
                    "VALUES (%s, %s, %s, %s, now()) "
                    "ON CONFLICT (user_id, metric_type, window_start) DO UPDATE SET "
                    "used_amount = veetee_quota_usage_buckets.used_amount "
                    "+ EXCLUDED.used_amount, updated_at = now()",
                    (user_id, metric_type, window_start, amount),
                )
        except Exception as exc:
            if self.is_quota_enabled(user_id):
                logger.error("Quota record_usage failed for user %s: %s", user_id, exc)
                raise QuotaServiceError(f"Quota enforcement unavailable: {exc}") from exc

    def get_effective_quota_and_usage(
        self, user_id: uuid.UUID, now: datetime | None = None
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        user_quota = self.quota_repo.get_user_quota(user_id)
        metrics = ["llm_tokens_day", "tts_chars_day", "tool_calls_minute", "rag_bytes_month"]

        usage_details: dict[str, Any] = {}
        for m in metrics:
            limit = self.get_effective_limit(user_id, m)
            window_start = get_quota_window_start(m, now)
            with self.database.connection() as conn:
                row = conn.execute(
                    "SELECT used_amount FROM veetee_quota_usage_buckets "
                    "WHERE user_id = %s AND metric_type = %s AND window_start = %s",
                    (user_id, m, window_start),
                ).fetchone()
                current_usage = cast(int, row[0]) if row else 0
            remaining = (limit - current_usage) if limit is not None else None
            if remaining is not None and remaining < 0:
                remaining = 0
            usage_details[m] = {
                "limit": limit,
                "used": current_usage,
                "remaining": remaining,
                "window_start": window_start.isoformat(),
            }

        return {
            "user_id": str(user_id),
            "enabled": self.is_quota_enabled(user_id),
            "policy": user_quota.to_dict() if user_quota else None,
            "metrics": usage_details,
        }
