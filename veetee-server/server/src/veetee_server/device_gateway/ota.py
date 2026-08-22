"""Firmware-compatible discovery, activation polling and OTA download."""

import hashlib
import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from veetee_server.app_context import request_id_context
from veetee_server.config import (
    Settings,
    get_effective_device_websocket_url,
    get_settings,
)
from veetee_server.device_gateway.protocol import parse_and_validate_json
from veetee_server.persistence import (
    ActivationRepository,
    DeviceRepository,
    FirmwareReleaseRepository,
)

ota_router = APIRouter()

_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def validate_ota_headers(
    headers: Mapping[str, str], id_max_length: int = 128
) -> tuple[bool, str | None, str | None]:
    """Validates baseline headers for OTA check.

    Required:
    - Device-Id: non-empty string <= id_max_length
    - Client-Id: non-empty string <= id_max_length
    Optional bounded:
    - User-Agent: <= 256 chars
    - Accept-Language: <= 128 chars
    """
    normalized = {k.lower(): v for k, v in headers.items()}

    device_id = normalized.get("device-id")
    if not device_id or not device_id.strip() or len(device_id.strip()) > id_max_length:
        return False, "veetee_invalid_input", "Missing or invalid Device-Id header"

    client_id = normalized.get("client-id")
    if not client_id or not client_id.strip() or len(client_id.strip()) > id_max_length:
        return False, "veetee_invalid_input", "Missing or invalid Client-Id header"

    user_agent = normalized.get("user-agent")
    if user_agent is not None and len(user_agent) > 256:
        return False, "veetee_invalid_input", "User-Agent header exceeds maximum length"

    accept_lang = normalized.get("accept-language")
    if accept_lang is not None and len(accept_lang) > 128:
        return False, "veetee_invalid_input", "Accept-Language header exceeds maximum length"

    return True, None, None


def _validate_dict_bounds(
    data: dict[str, Any], max_depth: int, current_depth: int = 1
) -> tuple[bool, str | None]:
    """Recursively validates dictionary bounds (max keys, string lengths, depth)."""
    if current_depth > max_depth:
        return False, "Payload exceeds maximum nesting depth"

    if len(data) > 32:
        return False, "Payload contains too many fields"

    for k, v in data.items():
        if not isinstance(k, str) or len(k) > 64:
            return False, "Payload key exceeds maximum length"

        if isinstance(v, str):
            if len(v) > 256:
                return False, "Payload string value exceeds maximum length"
        elif isinstance(v, dict):
            ok, reason = _validate_dict_bounds(v, max_depth, current_depth + 1)
            if not ok:
                return ok, reason
        elif isinstance(v, list):
            if current_depth + 1 > max_depth:
                return False, "Payload exceeds maximum nesting depth"
            if len(v) > 32:
                return False, "Payload array contains too many elements"
            for item in v:
                if isinstance(item, dict):
                    ok, reason = _validate_dict_bounds(item, max_depth, current_depth + 2)
                    if not ok:
                        return ok, reason
                elif isinstance(item, str) and len(item) > 256:
                    return False, "Payload array item exceeds maximum length"
                elif isinstance(item, (int, float, bool, type(None))):
                    pass
                else:
                    return False, "Payload array contains unsupported value type"
        elif isinstance(v, (int, float, bool, type(None))):
            pass
        else:
            return False, f"Unsupported data type for key '{k}'"

    return True, None


def _make_http_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(_CORS_HEADERS)
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "request_id": request_id,
        },
        headers=response_headers,
    )


def _get_request_id(request: Request) -> str:
    ctx_id = request_id_context.get()
    if ctx_id:
        return ctx_id
    header_id = request.headers.get("X-Veetee-Request-Id")
    if header_id:
        return header_id
    return ""


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    """Reads the request body while capping memory at ``max_bytes`` bytes."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@ota_router.options("/ota/check")
async def ota_check_options() -> Response:
    return Response(
        status_code=204,
        headers={
            **_CORS_HEADERS,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Device-Id, Client-Id, User-Agent, Accept-Language, Content-Type, "
                "X-Veetee-Request-Id"
            ),
        },
    )


@ota_router.get("/ota/check")
async def ota_check_get(request: Request) -> JSONResponse:
    req_id = _get_request_id(request)
    settings: Settings = getattr(request.app.state, "settings", get_settings())

    is_valid, err_code, err_msg = validate_ota_headers(
        request.headers, id_max_length=settings.id_max_length
    )
    if not is_valid:
        return _make_http_error(
            status_code=400,
            code=err_code or "veetee_invalid_input",
            message=err_msg or "Invalid headers",
            request_id=req_id,
        )

    return _build_ota_check_response(request, settings, {})


@ota_router.post("/ota/check")
async def ota_check_post(request: Request) -> JSONResponse:
    req_id = _get_request_id(request)
    settings: Settings = getattr(request.app.state, "settings", get_settings())

    is_valid, err_code, err_msg = validate_ota_headers(
        request.headers, id_max_length=settings.id_max_length
    )
    if not is_valid:
        return _make_http_error(
            status_code=400,
            code=err_code or "veetee_invalid_input",
            message=err_msg or "Invalid headers",
            request_id=req_id,
        )

    try:
        raw_body = await _read_bounded_body(request, settings.json_max_bytes)
    except ValueError:
        return _make_http_error(
            status_code=413,
            code="veetee_payload_too_large",
            message=f"Request payload size limit exceeded (max {settings.json_max_bytes} bytes)",
            request_id=req_id,
        )

    payload: dict[str, Any] = {}
    if raw_body:
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            return _make_http_error(
                status_code=415,
                code="veetee_invalid_input",
                message="Content-Type must be application/json",
                request_id=req_id,
            )

        try:
            parsed_payload = parse_and_validate_json(
                raw_body,
                max_depth=settings.json_max_depth,
            )
        except ValueError as exc:
            msg = (
                "JSON body must be an object"
                if "object" in str(exc)
                else "Invalid JSON payload syntax or depth limit exceeded"
            )
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message=msg,
                request_id=req_id,
            )
        except Exception:
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message="Invalid JSON payload syntax",
                request_id=req_id,
            )

        if not isinstance(parsed_payload, dict):
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message="JSON body must be an object",
                request_id=req_id,
            )

        payload = parsed_payload
        bounds_ok, bounds_reason = _validate_dict_bounds(payload, max_depth=settings.json_max_depth)
        if not bounds_ok:
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message=bounds_reason or "Payload structure constraints violated",
                request_id=req_id,
            )

    return _build_ota_check_response(request, settings, payload)


def _bounded_string(value: object, *, max_length: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    return normalized if 0 < len(normalized) <= max_length else ""


def _bounded_object(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict) or len(value) > 32:
        return {}
    return value


@dataclass(frozen=True, slots=True)
class FirmwareSystemInfo:
    board: str
    chip: str
    partition: str
    version: str


def parse_firmware_system_info(payload: Mapping[str, Any]) -> FirmwareSystemInfo:
    """Extracts the bounded fields emitted by the current firmware system-info."""
    application = _bounded_object(payload, "application")
    board = _bounded_object(payload, "board")
    ota = _bounded_object(payload, "ota")
    return FirmwareSystemInfo(
        board=(
            _bounded_string(board.get("type"), max_length=128)
            or _bounded_string(board.get("name"), max_length=128)
            or _bounded_string(payload.get("board"), max_length=128)
        ),
        chip=_bounded_string(payload.get("chip_model_name"), max_length=64),
        partition=(
            _bounded_string(ota.get("label"), max_length=64)
            or _bounded_string(payload.get("partition"), max_length=64)
            or "app"
        ),
        version=(
            _bounded_string(application.get("version"), max_length=64)
            or _bounded_string(payload.get("version"), max_length=64)
        ),
    )


def _artifact_url(settings: Settings, artifact_id: UUID) -> str:
    if not settings.ota_public_base_url:
        raise RuntimeError("OTA public base URL is not configured")
    return f"{settings.ota_public_base_url}/api/v1/devices/ota/artifacts/{artifact_id}"


def _build_ota_check_response(
    request: Request, settings: Settings, payload: dict[str, Any]
) -> JSONResponse:
    # The baseline firmware (references ota.cc) parses server_time.timestamp as
    # epoch milliseconds: tv_sec = ts / 1000, tv_usec = ts % 1000. Returning
    # seconds would set the device clock to ~1970, so milliseconds are required.
    now_ts_ms = int(round(time.time() * 1000))
    try:
        offset = datetime.now(UTC).astimezone().utcoffset()
        tz_offset = int(offset.total_seconds() / 60) if offset is not None else 420
    except Exception:
        tz_offset = 0

    websocket_url = get_effective_device_websocket_url(settings)

    content: dict[str, Any] = {
        "server_time": {
            "timestamp": now_ts_ms,
            "timezone_offset": tz_offset,
        },
        "websocket": {
            "url": websocket_url,
            "token": settings.device_gateway_token,
            "version": 1,
        },
        "firmware": {
            "version": "",
            "url": "",
        },
    }

    device_id = request.headers["Device-Id"].strip()
    client_id = request.headers["Client-Id"].strip()
    device_repository = getattr(request.app.state, "device_repository", None)
    activation_repository = getattr(request.app.state, "activation_repository", None)
    firmware_repository = getattr(request.app.state, "firmware_repository", None)
    if isinstance(device_repository, DeviceRepository):
        system_info = parse_firmware_system_info(payload)
        device = device_repository.get_by_device_id(device_id)
        if device is None and isinstance(activation_repository, ActivationRepository):
            activation = activation_repository.get_or_create(
                device_id,
                client_id,
                system_info.board,
                system_info.chip,
                system_info.partition,
                system_info.version,
                settings.activation_ttl_seconds,
            )
            remaining_ms = max(
                1,
                int((activation.expires_at - datetime.now(UTC)).total_seconds() * 1000),
            )
            content["activation"] = {
                "message": f"{settings.activation_console_url}\n{activation.code}",
                "code": activation.code,
                "challenge": activation.challenge,
                "timeout_ms": remaining_ms,
            }
        elif device is not None and isinstance(firmware_repository, FirmwareReleaseRepository):
            if device.client_id != client_id:
                return _make_http_error(
                    409,
                    "veetee_device_identity_conflict",
                    "Device identity does not match the bound device",
                    _get_request_id(request),
                )
            device = device_repository.record_check(
                device_id,
                system_info.board,
                system_info.chip,
                system_info.partition,
                system_info.version,
            ) or device
            board = system_info.board or device.board
            chip = system_info.chip or device.chip
            partition = system_info.partition or device.partition_name
            version = system_info.version or device.firmware_version
            release = firmware_repository.find_eligible(
                device.owner_user_id, board, chip, partition, version
            )
            if release is not None:
                content["firmware"] = {
                    "version": release.version,
                    "url": _artifact_url(settings, release.artifact_id),
                    # Current firmware checks cJSON_IsNumber(force), not booleans.
                    "force": int(release.force),
                }
    return JSONResponse(status_code=200, content=content, headers=dict(_CORS_HEADERS))


@ota_router.post("/ota/check/activate")
async def ota_activation_poll(request: Request) -> JSONResponse:
    request_id = _get_request_id(request)
    settings: Settings = getattr(request.app.state, "settings", get_settings())
    valid, code, message = validate_ota_headers(
        request.headers, id_max_length=settings.id_max_length
    )
    if not valid:
        return _make_http_error(
            400, code or "veetee_invalid_input", message or "Invalid headers", request_id
        )
    try:
        raw_body = await _read_bounded_body(request, settings.json_max_bytes)
    except ValueError:
        return _make_http_error(
            413, "veetee_payload_too_large", "Request payload too large", request_id
        )
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return _make_http_error(
            415, "veetee_invalid_input", "Content-Type must be application/json", request_id
        )
    try:
        payload = parse_and_validate_json(raw_body, max_depth=settings.json_max_depth)
    except Exception:
        return _make_http_error(
            400, "veetee_invalid_input", "Body must be an empty JSON object", request_id
        )
    if payload != {}:
        return _make_http_error(
            400, "veetee_invalid_input", "Body must be an empty JSON object", request_id
        )

    repository = getattr(request.app.state, "device_repository", None)
    if not isinstance(repository, DeviceRepository):
        return _make_http_error(503, "veetee_unavailable", "Persistence is not enabled", request_id)
    device = repository.get_by_device_id(request.headers["Device-Id"].strip())
    if device is not None and device.client_id != request.headers["Client-Id"].strip():
        return _make_http_error(
            409,
            "veetee_device_identity_conflict",
            "Device identity does not match the bound device",
            request_id,
        )
    return JSONResponse(
        status_code=200 if device is not None else 202,
        content={"activated": device is not None},
        headers=dict(_CORS_HEADERS),
    )


@ota_router.get("/ota/artifacts/{artifact_id}")
def download_artifact(request: Request, artifact_id: UUID) -> Response:
    repository = getattr(request.app.state, "firmware_repository", None)
    settings: Settings = getattr(request.app.state, "settings", get_settings())
    if not isinstance(repository, FirmwareReleaseRepository):
        return Response(status_code=404)
    release = repository.get_by_artifact(artifact_id)
    if release is None or Path(release.storage_name).name != release.storage_name:
        return Response(status_code=404)
    root = Path(settings.ota_artifact_dir).resolve()
    path = (root / release.storage_name).resolve()
    if path.parent != root or not path.is_file() or path.stat().st_size != release.file_size:
        return Response(status_code=404)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return Response(status_code=404)
    if not hmac.compare_digest(digest.hexdigest(), release.sha256):
        return Response(status_code=409)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"{release.version}.bin",
        headers={"ETag": f'"{release.sha256}"', "Cache-Control": "public, immutable"},
    )
