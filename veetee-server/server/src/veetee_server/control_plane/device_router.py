"""Control plane device management APIs (list, bind, patch, unbind)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from veetee_server.config import (
    Settings,
    get_effective_activation_secret,
    get_effective_device_jwt_secret,
)
from veetee_server.control_plane.router import current_user
from veetee_server.control_plane.schemas import (
    DeviceBindRequest,
    DevicePatchRequest,
    DeviceProvisionRequest,
    DeviceRecoveryRequest,
    DeviceRecoveryResponse,
    DeviceResponse,
)
from veetee_server.domain.device_lifecycle import (
    BindingConflictError,
    ExpiredCodeError,
    InvalidCodeError,
    MaxAttemptsExceededError,
)
from veetee_server.persistence import UserRepository
from veetee_server.persistence.device_repository import DeviceRepository

device_control_router = APIRouter(prefix="/api/v1/control/devices", tags=["control-plane-devices"])


@device_control_router.post("/provision", response_model=DeviceResponse, status_code=201)
def provision_device(
    payload: DeviceProvisionRequest,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
) -> dict[str, Any]:
    if user_repo.get_role(user_id) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to provision devices")
    try:
        return dev_repo.provision_enrollment(
            user_id,
            payload.device_id,
            payload.client_id,
            payload.ed25519_public_key,
            payload.board,
            payload.chip,
            payload.partition,
        )
    except BindingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _device_repo(request: Request) -> DeviceRepository:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return DeviceRepository(database)


def _user_repo(request: Request) -> UserRepository:
    repo = getattr(request.app.state, "user_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    if not isinstance(repo, UserRepository):
        raise HTTPException(status_code=503, detail="Persistence repository is invalid")
    return repo


@device_control_router.get("", response_model=list[DeviceResponse])
def list_devices(
    request: Request,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
) -> list[dict[str, Any]]:
    role = user_repo.get_role(user_id)
    if role == "admin":
        devices = dev_repo.list_all()
    else:
        devices = dev_repo.list_by_owner(user_id)

    registry = getattr(request.app.state, "device_session_registry", None)
    result = []
    for d in devices:
        is_online = False
        if registry is not None and hasattr(registry, "is_online"):
            try:
                is_online = bool(registry.is_online(d["device_id"]))
            except Exception:
                is_online = False
        d_copy = dict(d)
        d_copy["online"] = is_online
        result.append(d_copy)
    return result


@device_control_router.post("/bind", response_model=DeviceResponse)
def bind_device(
    request: Request,
    payload: DeviceBindRequest,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Valid Idempotency-Key is required")
    act_secret = get_effective_activation_secret(settings)
    try:
        device = dev_repo.bind_device(
            user_id=user_id,
            device_id=payload.device_id,
            code=payload.code,
            secret=act_secret,
            alias=payload.alias,
            agent_id=payload.agent_id,
            idempotency_key=idempotency_key,
        )
        return device
    except ExpiredCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MaxAttemptsExceededError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BindingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@device_control_router.patch("/{device_id}", response_model=DeviceResponse)
def patch_device(
    request: Request,
    device_id: str,
    payload: DevicePatchRequest,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
) -> dict[str, Any]:
    existing = dev_repo.get_by_device_id(device_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")

    role = user_repo.get_role(user_id)
    if role != "admin" and existing.get("owner_user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this device")
    if payload.agent_id is not None and not dev_repo.agent_belongs_to_owner(
        payload.agent_id, existing["owner_user_id"]
    ):
        raise HTTPException(status_code=400, detail="Agent does not belong to device owner")

    updated = dev_repo.patch_device(
        actor_user_id=user_id,
        device_id=device_id,
        alias=payload.alias,
        agent_id=payload.agent_id,
        auto_update=payload.auto_update,
        channel=payload.channel,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Device not found")
    return updated


@device_control_router.post("/{device_id}/recover", response_model=DeviceRecoveryResponse)
def recover_device_client(
    request: Request,
    device_id: str,
    payload: DeviceRecoveryRequest,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
) -> dict[str, Any]:
    existing = dev_repo.get_by_device_id(device_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if user_repo.get_role(user_id) != "admin" and existing.get("owner_user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to recover this device")
    try:
        device, token = dev_repo.recover_client(
            user_id,
            device_id,
            payload.client_id,
            is_admin=user_repo.get_role(user_id) == "admin",
            ttl_seconds=request.app.state.settings.device_ws_token_ttl_seconds,
            secret=get_effective_device_jwt_secret(request.app.state.settings),
        )
        return {"device": device, "recovery_token": token}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BindingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@device_control_router.post("/{device_id}/unbind", response_model=DeviceResponse)
def unbind_device(
    request: Request,
    device_id: str,
    user_id: Annotated[UUID, Depends(current_user)],
    dev_repo: Annotated[DeviceRepository, Depends(_device_repo)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Valid Idempotency-Key is required")
    existing = dev_repo.get_by_device_id(device_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found")

    role = user_repo.get_role(user_id)
    if existing.get("owner_user_id") is None:
        if role != "admin" and not dev_repo.was_unbound_by(device_id, user_id):
            raise HTTPException(status_code=403, detail="Not authorized to unbind this device")
        try:
            return dev_repo.unbind_device(
                actor_user_id=user_id,
                device_id=device_id,
                is_admin=role == "admin",
                idempotency_key=idempotency_key,
            )
        except BindingConflictError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    if role != "admin" and existing.get("owner_user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to unbind this device")

    try:
        return dev_repo.unbind_device(
            actor_user_id=user_id,
            device_id=device_id,
            is_admin=role == "admin",
            idempotency_key=idempotency_key,
        )
    except BindingConflictError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
