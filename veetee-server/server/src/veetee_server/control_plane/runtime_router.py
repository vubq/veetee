"""Tenant-scoped device and conversation metadata endpoints."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from veetee_server.config import Settings, get_settings
from veetee_server.device_gateway import DeviceSessionRegistry
from veetee_server.persistence import (
    ActivationRepository,
    DeviceRepository,
    FirmwareReleaseRepository,
)

from .router import AgentRepositoryDependency, CurrentUser
from .schemas import DeviceBindRequest, FirmwareReleaseCreate

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-runtime"])


def _device_repository(request: Request) -> DeviceRepository:
    repository = getattr(request.app.state, "device_repository", None)
    if not isinstance(repository, DeviceRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


def _activation_repository(request: Request) -> ActivationRepository:
    repository = getattr(request.app.state, "activation_repository", None)
    if not isinstance(repository, ActivationRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


def _firmware_repository(request: Request) -> FirmwareReleaseRepository:
    repository = getattr(request.app.state, "firmware_repository", None)
    if not isinstance(repository, FirmwareReleaseRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


@router.get("/devices")
async def list_devices(
    request: Request,
    user_id: CurrentUser,
) -> list[dict[str, Any]]:
    repository = _device_repository(request)
    registry = getattr(request.app.state, "device_session_registry", None)
    online = (
        await registry.online_device_ids()
        if isinstance(registry, DeviceSessionRegistry)
        else set()
    )
    return [device.to_dict(device.device_id in online) for device in repository.list(user_id)]


@router.post("/devices/bind")
def bind_device(
    request: Request, payload: DeviceBindRequest, user_id: CurrentUser
) -> dict[str, Any]:
    settings: Settings = getattr(request.app.state, "settings", get_settings())
    device, error = _activation_repository(request).bind_device(
        user_id,
        payload.agent_id,
        payload.code,
        rate_limit=settings.activation_bind_rate_limit,
        rate_window_seconds=settings.activation_bind_rate_window_seconds,
        receipt_ttl_seconds=settings.activation_bind_receipt_ttl_seconds,
    )
    if error == "agent_not_found":
        raise HTTPException(status_code=404, detail="Agent not found")
    if error == "rate_limited":
        raise HTTPException(status_code=429, detail="Binding attempt quota exceeded")
    if error in {"already_bound_conflict"}:
        raise HTTPException(status_code=409, detail="Device is already bound")
    if error == "expired_code":
        raise HTTPException(status_code=410, detail="Activation is no longer available")
    if error or device is None:
        raise HTTPException(status_code=400, detail="Invalid activation code")
    return device.to_dict()


@router.delete("/devices/{device_id}", status_code=204)
def unbind_device(request: Request, device_id: UUID, user_id: CurrentUser) -> None:
    if _device_repository(request).delete(user_id, device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")


@router.post("/ota/artifacts", status_code=201)
async def upload_artifact(
    request: Request,
    user_id: CurrentUser,
    content_type: Annotated[str | None, Header()] = None,
    content_length: Annotated[int | None, Header(ge=1)] = None,
) -> dict[str, Any]:
    settings: Settings = getattr(request.app.state, "settings", get_settings())
    if content_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail="Content-Type must be application/octet-stream")
    if content_length is not None and content_length > settings.ota_max_artifact_bytes:
        raise HTTPException(status_code=413, detail="Artifact exceeds configured size limit")

    root = Path(settings.ota_artifact_dir).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage_name = f"{os.urandom(16).hex()}.bin"
    temporary = root / f".{storage_name}.upload"
    final = root / storage_name
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.ota_max_artifact_bytes:
                    raise HTTPException(
                        status_code=413, detail="Artifact exceeds configured size limit"
                    )
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise HTTPException(status_code=400, detail="Artifact must not be empty")
        os.replace(temporary, final)
        final.chmod(0o400)
        artifact_id = _firmware_repository(request).create_artifact(
            user_id, storage_name, size, digest.hexdigest()
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        if final.exists():
            final.unlink()
        raise
    return {"id": artifact_id, "size": size, "sha256": digest.hexdigest()}


@router.post("/ota/releases", status_code=201)
def create_release(
    request: Request, payload: FirmwareReleaseCreate, user_id: CurrentUser
) -> dict[str, Any]:
    try:
        release = _firmware_repository(request).create_release(
            user_id,
            payload.artifact_id,
            payload.version,
            payload.board,
            payload.chip,
            payload.partition,
            payload.force,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Artifact not found") from None
    return release.to_dict()


@router.post("/ota/releases/{release_id}/publish")
def publish_release(request: Request, release_id: UUID, user_id: CurrentUser) -> dict[str, Any]:
    release = _firmware_repository(request).publish(user_id, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release.to_dict()


@router.get("/conversations")
def list_conversations(
    user_id: CurrentUser,
    repository: AgentRepositoryDependency,
    agent_id: UUID | None = None,
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, agent_id, device_id, title, summary, locale, turn_count, "
        "started_at, ended_at FROM veetee_conversations WHERE owner_user_id = %s"
    )
    params: list[Any] = [user_id]
    if agent_id is not None:
        query += " AND agent_id = %s"
        params.append(agent_id)
    query += " ORDER BY started_at DESC"
    with repository.database.connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "id": row[0],
            "agent_id": row[1],
            "device_id": row[2],
            "title": row[3],
            "summary": row[4],
            "locale": row[5],
            "turn_count": row[6],
            "started_at": row[7],
            "ended_at": row[8],
        }
        for row in rows
    ]
