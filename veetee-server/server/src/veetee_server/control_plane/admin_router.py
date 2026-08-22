"""Admin Control Plane router (M6.8).

User management, password reset issuance & consumption, typed global settings,
audit log search, and quota policy management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr

from veetee_server.persistence import (
    AdminUserRepository,
    AuditLogRepository,
    QuotaRepository,
    QuotaService,
    SystemSettingsRepository,
)

from .router import AdminActor, CurrentActor

router = APIRouter(tags=["control-plane-admin"])


class AdminUserCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    role: Literal["owner", "admin"]
    status: Literal["active", "suspended"] = "active"


class AdminUserUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    role: Literal["owner", "admin"] | None = None
    status: Literal["active", "suspended"] | None = None


class PasswordResetRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=256)
    new_password: SecretStr = Field(..., min_length=12, max_length=1024)


class SettingUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    value: Any


class QuotaPolicyUpdate(BaseModel):
    expected_version: int = Field(..., ge=1)
    llm_tokens_per_day: int | None = Field(None, ge=0, le=9_223_372_036_854_775_807)
    tts_chars_per_day: int | None = Field(None, ge=0, le=9_223_372_036_854_775_807)
    tool_calls_per_minute: int | None = Field(None, ge=0, le=2_147_483_647)
    rag_bytes_per_month: int | None = Field(None, ge=0, le=9_223_372_036_854_775_807)
    enabled: bool = False


def _admin_user_repo(request: Request) -> AdminUserRepository:
    repo = getattr(request.app.state, "admin_user_repository", None)
    if not isinstance(repo, AdminUserRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repo


def _settings_repo(request: Request) -> SystemSettingsRepository:
    repo = getattr(request.app.state, "system_settings_repository", None)
    if not isinstance(repo, SystemSettingsRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repo


def _audit_log_repo(request: Request) -> AuditLogRepository:
    repo = getattr(request.app.state, "audit_log_repository", None)
    if not isinstance(repo, AuditLogRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repo


def _quota_repo(request: Request) -> QuotaRepository:
    repo = getattr(request.app.state, "quota_repository", None)
    if not isinstance(repo, QuotaRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repo


def _quota_service(request: Request) -> QuotaService:
    service = getattr(request.app.state, "quota_service", None)
    if not isinstance(service, QuotaService):
        raise HTTPException(status_code=503, detail="Quota service is unavailable")
    return service


# --- User Management Endpoints ---


@router.get("/api/v1/control/admin/users")
def list_users(
    request: Request,
    admin: AdminActor,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    role: Literal["owner", "admin"] | None = Query(None),
    status: Literal["active", "suspended"] | None = Query(None),
    search: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    repo = _admin_user_repo(request)
    users, total = repo.list_users(page=page, limit=limit, role=role, status=status, search=search)
    return {
        "items": [u.to_dict() for u in users],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.post("/api/v1/control/admin/users", status_code=201)
def create_user(
    request: Request,
    payload: AdminUserCreate,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _admin_user_repo(request)
    user, reset_token, error = repo.create_user(
        creator_id=admin.user_id,
        email=payload.email,
        role=payload.role,
        status=payload.status,
    )
    if error == "invalid_email":
        raise HTTPException(status_code=422, detail="Invalid email format")
    if error == "invalid_role":
        raise HTTPException(status_code=422, detail="Role must be owner or admin")
    if error == "invalid_status":
        raise HTTPException(status_code=422, detail="Status must be active or suspended")
    if error == "duplicate_email":
        raise HTTPException(status_code=409, detail="User email already exists")
    if user is None or reset_token is None:
        raise HTTPException(status_code=500, detail="Failed to create user")

    return {
        "user": user.to_dict(),
        "reset_token": reset_token,
    }


@router.put("/api/v1/control/admin/users/{user_id}")
def update_user(
    request: Request,
    user_id: UUID,
    payload: AdminUserUpdate,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _admin_user_repo(request)
    user, error = repo.update_user(
        actor_id=admin.user_id,
        target_user_id=user_id,
        expected_version=payload.expected_version,
        role=payload.role,
        status=payload.status,
    )
    if error == "not_found":
        raise HTTPException(status_code=404, detail="User not found")
    if error == "cannot_suspend_self":
        raise HTTPException(status_code=409, detail="Admin cannot suspend self")
    if error == "last_admin_lockout":
        raise HTTPException(
            status_code=409, detail="Cannot suspend or demote the last active admin"
        )
    if error == "version_conflict":
        raise HTTPException(status_code=409, detail="User was modified by another request")
    if error in ("invalid_role", "invalid_status"):
        raise HTTPException(status_code=422, detail="Invalid role or status parameter")
    if user is None:
        raise HTTPException(status_code=500, detail="Failed to update user")

    return user.to_dict()


@router.post("/api/v1/control/admin/users/{user_id}/reset-token")
def issue_user_reset_token(
    request: Request,
    user_id: UUID,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _admin_user_repo(request)
    raw_token, error = repo.issue_reset_token(
        creator_id=admin.user_id,
        target_user_id=user_id,
    )
    if error == "user_not_found":
        raise HTTPException(status_code=404, detail="User not found")
    if raw_token is None:
        raise HTTPException(status_code=500, detail="Failed to issue reset token")

    return {
        "reset_token": raw_token,
        "expires_in_seconds": 86400,
    }


@router.post("/api/v1/control/auth/reset-password")
def reset_password(
    request: Request,
    payload: PasswordResetRequest,
) -> dict[str, Any]:
    repo = _admin_user_repo(request)
    success, error = repo.consume_reset_token(
        raw_token=payload.token,
        new_password=payload.new_password.get_secret_value(),
    )
    if not success:
        if error == "password_too_short":
            raise HTTPException(status_code=422, detail="Password must be at least 12 characters")
        raise HTTPException(
            status_code=400, detail="Invalid, expired, or already used password reset token"
        )

    return {"status": "ok", "message": "Password reset successfully"}


# --- System Settings Endpoints ---


@router.get("/api/v1/control/admin/settings")
def list_settings(
    request: Request,
    admin: AdminActor,
) -> list[dict[str, Any]]:
    repo = _settings_repo(request)
    return [s.to_dict() for s in repo.list_settings()]


@router.get("/api/v1/control/admin/settings/{key}")
def get_setting(
    request: Request,
    key: str,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _settings_repo(request)
    setting = repo.get_setting(key)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting key not found or not allowlisted")
    return setting.to_dict()


@router.put("/api/v1/control/admin/settings/{key}")
def update_setting(
    request: Request,
    key: str,
    payload: SettingUpdate,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _settings_repo(request)
    setting, error = repo.update_setting(
        actor_id=admin.user_id,
        key=key,
        value=payload.value,
        expected_version=payload.expected_version,
    )
    if error == "invalid_key":
        raise HTTPException(status_code=404, detail="Setting key is not allowlisted")
    if error == "invalid_value_type":
        raise HTTPException(status_code=422, detail="Setting value does not match schema type")
    if error == "negative_value":
        raise HTTPException(status_code=422, detail="Setting value cannot be negative")
    if error == "version_conflict":
        raise HTTPException(status_code=409, detail="Setting was modified by another request")
    if setting is None:
        raise HTTPException(status_code=500, detail="Failed to update setting")

    return setting.to_dict()


# --- Audit Logs Endpoint ---


@router.get("/api/v1/control/admin/audit-logs")
def search_audit_logs(
    request: Request,
    admin: AdminActor,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    action: str | None = Query(None, max_length=128),
    resource_type: str | None = Query(None, max_length=64),
    actor_user_id: UUID | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(status_code=422, detail="start_time must be earlier than end_time")

    repo = _audit_log_repo(request)
    items, total = repo.search(
        page=page,
        limit=limit,
        action=action,
        resource_type=resource_type,
        actor_user_id=actor_user_id,
        start_time=start_time,
        end_time=end_time,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
    }


# --- Quota Governance Endpoints ---


@router.get("/api/v1/control/admin/quotas/{user_id}")
def get_user_quota(
    request: Request,
    user_id: UUID,
    admin: AdminActor,
) -> dict[str, Any]:
    if not _quota_repo(request).user_exists(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    service = _quota_service(request)
    return service.get_effective_quota_and_usage(user_id)


@router.put("/api/v1/control/admin/quotas/{user_id}")
def update_user_quota(
    request: Request,
    user_id: UUID,
    payload: QuotaPolicyUpdate,
    admin: AdminActor,
) -> dict[str, Any]:
    repo = _quota_repo(request)
    quota, error = repo.update_user_quota(
        actor_id=admin.user_id,
        target_user_id=user_id,
        expected_version=payload.expected_version,
        llm_tokens_per_day=payload.llm_tokens_per_day,
        tts_chars_per_day=payload.tts_chars_per_day,
        tool_calls_per_minute=payload.tool_calls_per_minute,
        rag_bytes_per_month=payload.rag_bytes_per_month,
        enabled=payload.enabled,
    )
    if error == "version_conflict":
        raise HTTPException(status_code=409, detail="Quota policy was modified by another request")
    if error == "user_not_found":
        raise HTTPException(status_code=404, detail="User not found")
    if error and error.startswith("invalid_"):
        raise HTTPException(status_code=422, detail=f"Quota value parameter is invalid: {error}")
    if quota is None:
        raise HTTPException(status_code=500, detail="Failed to update quota policy")

    return quota.to_dict()


@router.get("/api/v1/control/quotas/me")
def get_my_quota(
    request: Request,
    current_actor: CurrentActor,
) -> dict[str, Any]:
    service = _quota_service(request)
    return service.get_effective_quota_and_usage(current_actor.user_id)
