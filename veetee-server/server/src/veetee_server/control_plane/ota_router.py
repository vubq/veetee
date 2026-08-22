"""Authenticated control-plane APIs for immutable OTA content and rollouts."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from veetee_server.config import Settings
from veetee_server.control_plane.router import current_user
from veetee_server.control_plane.schemas import ReleaseCreateRequest, RollbackAuthorizeRequest
from veetee_server.domain.device_lifecycle import parse_semver
from veetee_server.persistence import UserRepository
from veetee_server.persistence.device_repository import OtaRepository

ota_control_router = APIRouter(prefix="/api/v1/control/ota", tags=["control-plane-ota"])
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{128}$")
_ALLOWED_MIME = {"application/octet-stream", "application/x-binary"}
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def _verify_signature(path: Path, signature_hex: str, public_key_hex: str) -> bool:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", public_key_hex):
        return False
    signature_path = path.with_suffix(".sig")
    public_key_path = path.with_suffix(".pub")
    try:
        signature_path.write_bytes(bytes.fromhex(signature_hex))
        public_key_path.write_bytes(_ED25519_SPKI_PREFIX + bytes.fromhex(public_key_hex))
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-keyform",
                "DER",
                "-inkey",
                str(public_key_path),
                "-rawin",
                "-in",
                str(path),
                "-sigfile",
                str(signature_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    finally:
        signature_path.unlink(missing_ok=True)
        public_key_path.unlink(missing_ok=True)


def _ota_repo(request: Request) -> OtaRepository:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return OtaRepository(database)


def _user_repo(request: Request) -> UserRepository:
    repo = getattr(request.app.state, "user_repository", None)
    if not isinstance(repo, UserRepository):
        raise HTTPException(status_code=503, detail="Persistence repository is invalid")
    return repo


def _require_admin(
    user_id: Annotated[UUID, Depends(current_user)],
    user_repo: Annotated[UserRepository, Depends(_user_repo)],
) -> UUID:
    if user_repo.get_role(user_id) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required for OTA operations")
    return user_id


AdminUser = Annotated[UUID, Depends(_require_admin)]
OtaRepo = Annotated[OtaRepository, Depends(_ota_repo)]


@ota_control_router.post("/artifacts", status_code=201)
async def upload_artifact(
    request: Request,
    user_id: AdminUser,
    ota_repo: OtaRepo,
    expected_sha256: Annotated[str, Header(alias="X-Artifact-SHA256")],
    signature: Annotated[str, Header(alias="X-Artifact-Signature")],
    file_name: Annotated[str, Header(alias="X-Artifact-Name")],
    board: Annotated[str, Header(alias="X-Artifact-Board")],
    chip: Annotated[str, Header(alias="X-Artifact-Chip")],
    partition: Annotated[str, Header(alias="X-Artifact-Partition")],
    provenance: Annotated[str, Header(alias="X-Artifact-Provenance")],
) -> dict[str, Any]:
    """Stream one firmware body to immutable storage while hashing it."""
    settings: Settings = request.app.state.settings
    digest = expected_sha256.lower()
    if not _SHA256_RE.fullmatch(digest):
        raise HTTPException(status_code=400, detail="A valid SHA-256 digest is required")
    if not _SIGNATURE_RE.fullmatch(signature.lower()):
        raise HTTPException(status_code=400, detail="A detached Ed25519 signature is required")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=415, detail="Unsupported artifact media type")
    content_length = request.headers.get("content-length")
    if content_length is None:
        raise HTTPException(status_code=411, detail="Content-Length is required")
    try:
        declared_length = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Content-Length must be a decimal integer"
        ) from exc
    if declared_length <= 0:
        raise HTTPException(status_code=400, detail="Content-Length must be positive")
    if declared_length > settings.ota_max_upload_bytes:
        raise HTTPException(status_code=413, detail="Artifact exceeds upload limit")
    if not all(value.strip() for value in (file_name, board, chip, partition, provenance)):
        raise HTTPException(status_code=400, detail="Artifact target metadata is required")
    if len(provenance.strip()) > 512:
        raise HTTPException(status_code=400, detail="Artifact provenance exceeds maximum length")

    root = Path(settings.ota_artifact_dir).resolve()
    await run_in_threadpool(root.mkdir, 0o750, True, True)
    artifact_id = uuid.uuid4()
    temporary = root / f".{artifact_id}.upload"
    final = root / f"{artifact_id}.bin"
    hasher = hashlib.sha256()
    size = 0
    try:
        output = await run_in_threadpool(temporary.open, "xb")
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.ota_max_upload_bytes:
                    raise HTTPException(status_code=413, detail="Artifact exceeds upload limit")
                hasher.update(chunk)
                await run_in_threadpool(output.write, chunk)
            await run_in_threadpool(output.flush)
            await run_in_threadpool(os.fsync, output.fileno())
        finally:
            await run_in_threadpool(output.close)
        if size == 0:
            raise HTTPException(status_code=400, detail="Artifact must not be empty")
        if hasher.hexdigest() != digest:
            raise HTTPException(status_code=400, detail="SHA-256 checksum mismatch")
        if not await run_in_threadpool(
            _verify_signature, temporary, signature, settings.ota_ed25519_public_key
        ):
            raise HTTPException(status_code=400, detail="Ed25519 signature verification failed")
        await run_in_threadpool(os.link, temporary, final)
        await run_in_threadpool(temporary.unlink)
        artifact = await run_in_threadpool(
            ota_repo.create_artifact,
            artifact_id=artifact_id,
            board=board.strip(),
            chip=chip.strip(),
            partition=partition.strip(),
            file_name=Path(file_name).name,
            file_path=str(final),
            file_size=size,
            sha256=digest,
            signature=signature.lower(),
            signature_algorithm="ed25519",
            signature_key_id=settings.ota_signature_key_id,
            provenance=provenance.strip(),
            actor_user_id=user_id,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
    return artifact


@ota_control_router.get("/artifacts")
def list_artifacts(user_id: AdminUser, ota_repo: OtaRepo) -> list[dict[str, Any]]:
    return ota_repo.list_artifacts()


@ota_control_router.post("/releases", status_code=201)
def create_release(
    payload: ReleaseCreateRequest,
    user_id: AdminUser,
    ota_repo: OtaRepo,
) -> dict[str, Any]:
    try:
        parse_semver(payload.version)
        if payload.min_current_version:
            parse_semver(payload.min_current_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    artifact = ota_repo.get_artifact(payload.artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    target = (payload.board, payload.chip, payload.partition)
    artifact_target = (artifact["board"], artifact["chip"], artifact["partition"])
    if target != artifact_target:
        raise HTTPException(status_code=400, detail="Release target must match artifact target")
    try:
        release = ota_repo.create_release(
            version=payload.version,
            artifact_id=payload.artifact_id,
            board=payload.board,
            chip=payload.chip,
            partition=payload.partition,
            channel=payload.channel,
            min_current_version=payload.min_current_version,
            provenance=payload.provenance,
            rollback_target_id=payload.rollback_target_id,
            actor_user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return release


@ota_control_router.get("/releases")
def list_releases(user_id: AdminUser, ota_repo: OtaRepo) -> list[dict[str, Any]]:
    return ota_repo.list_releases()


@ota_control_router.post("/releases/{release_id}/publish")
def publish_release(
    release_id: UUID,
    user_id: AdminUser,
    ota_repo: OtaRepo,
    percentage: Annotated[int, Query(ge=0, le=100)] = 100,
) -> dict[str, Any]:
    release = ota_repo.publish_release(
        release_id, cohort_percentage=percentage, actor_user_id=user_id
    )
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


@ota_control_router.get("/rollouts")
def list_rollouts(user_id: AdminUser, ota_repo: OtaRepo) -> list[dict[str, Any]]:
    return ota_repo.list_rollouts()


def _change_rollout(
    ota_repo: OtaRepository, user_id: UUID, rollout_id: UUID, state: str
) -> dict[str, Any]:
    rollout = ota_repo.update_rollout_status(rollout_id, state, actor_user_id=user_id)
    if rollout is None:
        raise HTTPException(status_code=404, detail="Rollout not found")
    return rollout


@ota_control_router.post("/rollouts/{rollout_id}/pause")
def pause_rollout(rollout_id: UUID, user_id: AdminUser, ota_repo: OtaRepo) -> dict[str, Any]:
    return _change_rollout(ota_repo, user_id, rollout_id, "paused")


@ota_control_router.post("/rollouts/{rollout_id}/resume")
def resume_rollout(rollout_id: UUID, user_id: AdminUser, ota_repo: OtaRepo) -> dict[str, Any]:
    return _change_rollout(ota_repo, user_id, rollout_id, "active")


@ota_control_router.post("/rollouts/{rollout_id}/kill")
def kill_rollout(rollout_id: UUID, user_id: AdminUser, ota_repo: OtaRepo) -> dict[str, Any]:
    return _change_rollout(ota_repo, user_id, rollout_id, "killed")


@ota_control_router.post("/rollouts/{rollout_id}/rollback")
def rollback_rollout(
    rollout_id: UUID,
    payload: RollbackAuthorizeRequest,
    user_id: AdminUser,
    ota_repo: OtaRepo,
) -> dict[str, Any]:
    try:
        rollout = ota_repo.activate_rollback_target(
            rollout_id,
            user_id,
            scope=payload.scope,
            device_id=payload.device_id,
            cohort=payload.cohort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if rollout is None:
        raise HTTPException(status_code=404, detail="Rollout not found")
    return rollout


@ota_control_router.get("/summary")
def get_summary(user_id: AdminUser, ota_repo: OtaRepo) -> dict[str, Any]:
    return ota_repo.get_summary_counts()
