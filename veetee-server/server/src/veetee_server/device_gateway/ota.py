"""OTA and Config responder for Veetee Server M1.4.

Implements the minimal device-facing discovery endpoint that the baseline
firmware (pinned commit in firmware-compatibility-matrix.md) consumes:

- ``GET/POST /api/v1/devices/ota/check`` returns server time (epoch
  milliseconds, matching the baseline firmware ``ota.cc`` parse), the WebSocket
  URL/token/version and a no-update ``firmware`` object.
- ``OPTIONS`` exposes a credentials-free CORS policy.

No release catalog or rollout is implemented in M1.4; the ``firmware`` object
always reports no update (``version: ""`` and ``url: ""``) which the baseline
firmware treats as "no new version" (``Ota::IsNewVersionAvailable`` returns
false for an empty new version).
"""

import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from veetee_server.app_context import request_id_context
from veetee_server.config import (
    Settings,
    get_effective_device_websocket_url,
    get_settings,
)
from veetee_server.device_gateway.protocol import parse_and_validate_json

logger = logging.getLogger("veetee.device_gateway.ota")

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

    return _build_ota_check_response(settings)


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

    return _build_ota_check_response(settings)


def _build_ota_check_response(settings: Settings) -> JSONResponse:
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

    content = {
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
