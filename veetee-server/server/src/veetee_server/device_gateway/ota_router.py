"""Authenticated device artifact download and OTA reporting endpoints."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from veetee_server.config import Settings, get_effective_device_jwt_secret
from veetee_server.control_plane.schemas import OtaReportCreateRequest
from veetee_server.domain.device_lifecycle import (
    ReportRateLimitError,
    verify_artifact_download_token,
    verify_device_ws_token,
)
from veetee_server.persistence.device_repository import DeviceCredentialRepository, OtaRepository

device_ota_router = APIRouter(prefix="/api/v1/devices/ota", tags=["device-ota"])
_RANGE_RE = re.compile(r"^bytes=(?:(\d+)-(\d*)|-(\d+))$")


def _bearer(value: str | None) -> str:
    if not value or not value.startswith("Bearer "):
        return ""
    return value[7:].strip()


def _single_range(value: str, size: int) -> tuple[int, int] | None:
    match = _RANGE_RE.fullmatch(value)
    if match is None or "," in value:
        return None
    start_text, end_text, suffix_text = match.groups()
    if suffix_text is not None:
        suffix = int(suffix_text)
        if suffix <= 0:
            return None
        return max(0, size - suffix), size - 1
    assert start_text is not None
    start = int(start_text)
    end = int(end_text) if end_text else size - 1
    if start >= size or end < start or end >= size:
        return None
    return start, end


def _stream(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                raise OSError("Artifact was truncated during download")
            remaining -= len(chunk)
            yield chunk


def _verified_artifact_path(root_value: str, artifact: dict[str, Any]) -> tuple[Path, int] | None:
    root = Path(root_value).resolve()
    path = Path(str(artifact["file_path"])).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    expected_size = int(artifact["file_size"])
    if not path.is_file() or path.stat().st_size != expected_size:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != artifact["sha256"]:
        return None
    return path, expected_size


def _authenticate_device(
    database: Any,
    device_id: str,
    client_id: str,
    token: str,
    secret: str,
) -> bool:
    claims = verify_device_ws_token(token, secret)
    return bool(
        claims is not None
        and claims.get("device_id") == device_id
        and claims.get("client_id") == client_id
        and DeviceCredentialRepository(database).verify_credential(
            device_id, client_id, token, secret
        )
    )


@device_ota_router.get("/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: UUID,
    request: Request,
    token: str,
) -> Response:
    settings: Settings = request.app.state.settings
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    device_id = verify_artifact_download_token(
        token, artifact_id, get_effective_device_jwt_secret(settings)
    )
    if device_id is None:
        raise HTTPException(status_code=403, detail="Invalid or expired artifact token")
    repository = OtaRepository(database)
    artifact = await run_in_threadpool(repository.get_artifact_for_device, artifact_id, device_id)
    if artifact is None:
        raise HTTPException(status_code=403, detail="Artifact is not eligible for device")

    verified = await run_in_threadpool(_verified_artifact_path, settings.ota_artifact_dir, artifact)
    if verified is None:
        raise HTTPException(status_code=404, detail="Artifact unavailable")
    path, expected_size = verified

    range_value = request.headers.get("range")
    start, end, status_code = 0, expected_size - 1, 200
    headers = {"Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
    if range_value:
        selected = _single_range(range_value, expected_size)
        if selected is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{expected_size}"})
        start, end = selected
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{expected_size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)
    return StreamingResponse(_stream(path, start, length), status_code=status_code, headers=headers)


@device_ota_router.post("/report", status_code=201)
async def report_ota_event(
    request: Request,
    authorization: str | None = Header(default=None),
    device_id: str | None = Header(default=None, alias="Device-Id"),
    client_id: str | None = Header(default=None, alias="Client-Id"),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    content_length = request.headers.get("content-length")
    if content_length is None:
        raise HTTPException(status_code=411, detail="Content-Length is required")
    try:
        declared_length = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Content-Length must be a decimal integer"
        ) from exc
    if declared_length < 0:
        raise HTTPException(status_code=400, detail="Content-Length must not be negative")
    if declared_length > settings.ota_report_max_bytes:
        raise HTTPException(status_code=413, detail="OTA report exceeds body limit")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.ota_report_max_bytes:
            raise HTTPException(status_code=413, detail="OTA report exceeds body limit")
    try:
        payload = OtaReportCreateRequest.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    token = _bearer(authorization)
    authenticated_device_id = device_id or ""
    authenticated = await run_in_threadpool(
        _authenticate_device,
        database,
        authenticated_device_id,
        client_id or "",
        token,
        get_effective_device_jwt_secret(settings),
    )
    if not authenticated:
        raise HTTPException(status_code=401, detail="Valid device credential required")
    repository = OtaRepository(database)
    try:
        return await run_in_threadpool(
            repository.record_report,
            payload.event_id,
            authenticated_device_id,
            payload.release_id,
            payload.version,
            payload.stage,
            payload.outcome,
            payload.error_message,
            payload.metadata,
            settings.ota_health_gate_failure_minimum,
            settings.ota_health_gate_sample_threshold,
            settings.ota_health_gate_failure_percentage,
            settings.ota_report_max_per_device_hour,
            settings.ota_report_dedupe_window_seconds,
        )
    except ReportRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={"code": "veetee_rate_limited", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
