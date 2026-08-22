"""OTA and Config responder for Veetee Server M5.

Implements device-facing discovery, activation challenges, per-device WS credential issuance,
and OTA firmware release discovery.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from veetee_server.app_context import request_id_context
from veetee_server.config import (
    Settings,
    get_effective_activation_secret,
    get_effective_device_jwt_secret,
    get_effective_device_websocket_url,
)
from veetee_server.device_gateway.ota_router import _bearer
from veetee_server.device_gateway.protocol import parse_and_validate_json
from veetee_server.domain.device_lifecycle import (
    ExpiredCodeError,
    InvalidCodeError,
    MaxAttemptsExceededError,
    create_artifact_download_token,
)
from veetee_server.persistence.database import PostgresDatabase
from veetee_server.persistence.device_repository import (
    ActivationRepository,
    DeviceCredentialRepository,
    DeviceRepository,
    OtaRepository,
)

logger = logging.getLogger("veetee.device_gateway.ota")

ota_router = APIRouter()

_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def validate_ota_headers(
    headers: Mapping[str, str], id_max_length: int = 128
) -> tuple[bool, str | None, str | None]:
    """Validates baseline headers for OTA check."""
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
                if isinstance(item, str) and len(item) > 256:
                    return False, "Payload array element exceeds maximum length"
                if isinstance(item, dict):
                    ok, reason = _validate_dict_bounds(item, max_depth, current_depth + 1)
                    if not ok:
                        return ok, reason

    return True, None


def _make_http_error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    headers = dict(_CORS_HEADERS)
    headers["X-Veetee-Request-Id"] = request_id
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "request_id": request_id},
        headers=headers,
    )


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError("payload_too_large")
    return bytes(body)


@ota_router.options("/ota/check")
async def ota_check_options() -> Response:
    """Credentials-free CORS preflight for discovery endpoint."""
    headers = dict(_CORS_HEADERS)
    headers.update(
        {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": (
                "Authorization, Device-Id, Client-Id, User-Agent, Accept-Language, Content-Type, "
                "X-Veetee-Request-Id"
            ),
            "Access-Control-Max-Age": "86400",
        }
    )
    return Response(status_code=204, headers=headers)


@ota_router.get("/ota/check")
@ota_router.post("/ota/check")
async def ota_check(request: Request) -> JSONResponse:
    req_id = request_id_context.get() or "unknown"
    settings: Settings = request.app.state.settings

    if (
        settings.persistence_enabled
        and settings.environment not in {"local", "test"}
        and request.url.scheme != "https"
    ):
        return _make_http_error(
            status_code=400,
            code="veetee_invalid_input",
            message="Production device discovery requires HTTPS",
            request_id=req_id,
        )

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

    payload_dict: dict[str, Any] = {}
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
            payload = parse_and_validate_json(
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

        if not isinstance(payload, dict):
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message="JSON body must be an object",
                request_id=req_id,
            )

        bounds_ok, bounds_reason = _validate_dict_bounds(payload, max_depth=settings.json_max_depth)
        if not bounds_ok:
            return _make_http_error(
                status_code=400,
                code="veetee_invalid_input",
                message=bounds_reason or "Payload structure constraints violated",
                request_id=req_id,
            )
        payload_dict = payload

    device_id = request.headers["device-id"].strip()
    client_id = request.headers["client-id"].strip()

    return await _build_ota_check_response(request, settings, device_id, client_id, payload_dict)


async def _build_ota_check_response(
    request: Request,
    settings: Settings,
    device_id: str,
    client_id: str,
    payload: dict[str, Any],
) -> JSONResponse:
    now_ts_ms = int(round(time.time() * 1000))
    try:
        offset = datetime.now(UTC).astimezone().utcoffset()
        tz_offset = int(offset.total_seconds() / 60) if offset is not None else 420
    except Exception:
        tz_offset = 0

    websocket_url = get_effective_device_websocket_url(settings)

    # Extract device system metadata if provided in request body
    sys_info = payload.get("system", {}) if isinstance(payload.get("system"), dict) else {}
    board = str(payload.get("board") or sys_info.get("board") or "").strip()
    chip = str(
        payload.get("chip_model_name")
        or payload.get("chip")
        or sys_info.get("chip_model_name")
        or sys_info.get("chip")
        or ""
    ).strip()
    partition = str(
        payload.get("partition")
        or payload.get("partition_label")
        or sys_info.get("partition")
        or sys_info.get("partition_label")
        or ""
    ).strip()
    firmware_version = str(
        payload.get("firmware_version") or payload.get("version") or sys_info.get("version") or ""
    ).strip()

    database: PostgresDatabase | None = getattr(request.app.state, "database", None)
    if not settings.persistence_enabled or database is None:
        # Compatibility fallback when persistence is disabled
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
        return JSONResponse(status_code=200, content=content, headers=dict(_CORS_HEADERS))

    # Persistence enabled: identify first, then authenticate before accepting bound telemetry.
    dev_repo = DeviceRepository(database)
    act_repo = ActivationRepository(database)
    cred_repo = DeviceCredentialRepository(database)
    ota_repo = OtaRepository(database)

    act_secret = get_effective_activation_secret(settings)
    jwt_secret = get_effective_device_jwt_secret(settings)

    existing = await run_in_threadpool(dev_repo.get_by_device_id, device_id)
    ws_token = ""
    if existing is None:
        if settings.allow_insecure_activation:
            device = await run_in_threadpool(
                dev_repo.get_or_create_unbound,
                device_id,
                client_id,
                board,
                chip,
                partition,
                firmware_version,
            )
        else:
            device = {"status": "activation_pending"}
    elif existing.get("status") == "unbound":
        device = existing
    elif existing.get("status") == "bound":
        try:
            device, ws_token = await run_in_threadpool(
                dev_repo.authenticate_observe_and_rotate,
                device_id,
                client_id,
                _bearer(request.headers.get("Authorization")),
                jwt_secret,
                settings.device_ws_token_ttl_seconds,
                board,
                chip,
                partition,
                firmware_version,
                settings.ota_discovery_min_interval_seconds,
            )
        except PermissionError as exc:
            return _make_http_error(
                401, "veetee_auth_failed", str(exc), request_id_context.get() or "unknown"
            )
        except RuntimeError as exc:
            return _make_http_error(
                429, "veetee_rate_limited", str(exc), request_id_context.get() or "unknown"
            )
    else:
        return _make_http_error(
            409,
            "veetee_recovery_required",
            "Device owner must complete authenticated client recovery",
            request_id_context.get() or "unknown",
        )

    firmware_info = {"version": "", "url": ""}
    activation_obj: dict[str, Any] | None = None

    if device.get("status") != "bound":
        nonce_header = request.headers.get("Activation-Nonce", "").strip()
        proof_header = request.headers.get("Activation-Proof", "").strip()
        if nonce_header or proof_header:
            if not nonce_header or not proof_header:
                return _make_http_error(
                    400,
                    "veetee_invalid_input",
                    "Activation-Nonce and Activation-Proof must be supplied together",
                    request_id_context.get() or "unknown",
                )
            try:
                code, verified = await run_in_threadpool(
                    act_repo.verify_enrollment_proof,
                    device_id,
                    client_id,
                    nonce_header,
                    proof_header,
                    act_secret,
                )
            except (ExpiredCodeError, InvalidCodeError, MaxAttemptsExceededError) as exc:
                return _make_http_error(
                    401,
                    "veetee_auth_failed",
                    str(exc),
                    request_id_context.get() or "unknown",
                )
            activation_obj = {
                "code": code,
                "message": "Display this code physically; enter it in the console to bind.",
                "timeout_ms": verified["timeout_ms"],
            }
            challenge_id = uuid.UUID(nonce_header)
            now = datetime.now(UTC)
            activation_obj["token"] = await run_in_threadpool(
                cred_repo.ensure_bootstrap_credential,
                device_id,
                client_id,
                challenge_id,
                now,
                now + timedelta(milliseconds=verified["timeout_ms"]),
                jwt_secret,
            )
        elif settings.allow_insecure_activation:
            code, challenge = await run_in_threadpool(
                act_repo.get_or_create_challenge,
                device_id,
                act_secret,
                settings.activation_code_ttl_seconds,
                settings.activation_max_attempts,
            )
            activation_obj = {
                "code": code,
                "challenge": str(challenge["id"]),
                "message": "Insecure local/test activation compatibility mode.",
                "timeout_ms": challenge["timeout_ms"],
            }
            if challenge["is_new"]:
                activation_obj["token"] = await run_in_threadpool(
                    cred_repo.ensure_bootstrap_credential,
                    device_id,
                    client_id,
                    challenge["id"],
                    challenge["created_at"],
                    challenge["expires_at"],
                    jwt_secret,
                )
        else:
            pending_challenge = await run_in_threadpool(
                act_repo.get_or_create_nonce,
                device_id,
                client_id,
                act_secret,
                settings.activation_code_ttl_seconds,
                settings.activation_max_attempts,
            )
            activation_obj = {
                "status": "pending",
                "message": "Device enrollment proof is required.",
            }
            if pending_challenge is not None and pending_challenge["nonce"]:
                activation_obj.update(
                    {
                        "nonce": pending_challenge["nonce"],
                        "timeout_ms": pending_challenge["timeout_ms"],
                    }
                )
    else:
        if device.get("client_id") != client_id:
            return _make_http_error(
                403,
                "veetee_auth_failed",
                "Client identity does not match bound device",
                request_id_context.get() or "unknown",
            )
        eligible = None
        if device.get("auto_update") and device.get("partition"):
            eligible = await run_in_threadpool(
                ota_repo.get_eligible_release,
                device_id,
                device.get("board", ""),
                device.get("chip", ""),
                device.get("partition", ""),
                device.get("current_firmware_version", ""),
                device.get("channel", "stable"),
            )
        if eligible:
            release, artifact = eligible
            download_token = create_artifact_download_token(
                artifact_id=artifact["id"],
                device_id=device_id,
                ttl_seconds=settings.ota_download_token_ttl_seconds,
                secret=jwt_secret,
            )
            base_url = settings.ota_public_base_url
            if not base_url and not settings.persistence_enabled:
                base_url = str(request.base_url).rstrip("/")
            artifact_url = f"{base_url}/api/v1/devices/ota/artifacts/{artifact['id']}"
            firmware_info = {
                "version": release["version"],
                "url": f"{artifact_url}?token={download_token}",
                "sha256": artifact["sha256"],
                "signature": artifact["signature"],
                "signature_algorithm": artifact["signature_algorithm"],
                "signature_key_id": artifact["signature_key_id"],
                "size": artifact["file_size"],
                "release_id": str(release["id"]),
            }

    res_content: dict[str, Any] = {
        "server_time": {
            "timestamp": now_ts_ms,
            "timezone_offset": tz_offset,
        },
        "websocket": {
            "url": websocket_url,
            "token": ws_token,
            "version": 1,
        },
        "firmware": firmware_info,
    }
    if activation_obj is not None:
        res_content["activation"] = activation_obj

    return JSONResponse(status_code=200, content=res_content, headers=dict(_CORS_HEADERS))
