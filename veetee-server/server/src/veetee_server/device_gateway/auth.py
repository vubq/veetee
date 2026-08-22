"""Header and token authentication for Device WebSocket handshake."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import NamedTuple

from veetee_server.domain.device_lifecycle import verify_device_ws_token
from veetee_server.persistence.database import PostgresDatabase
from veetee_server.persistence.device_repository import DeviceCredentialRepository, DeviceRepository


class AuthResult(NamedTuple):
    is_valid: bool
    error_code: str | None
    error_message: str | None
    device_id: str | None
    client_id: str | None
    protocol_version: int = 1


def validate_handshake_headers(
    headers: Mapping[str, str],
    expected_token: str,
    id_max_length: int = 128,
    jwt_secret: str = "",
    persistence_enabled: bool = False,
    database: PostgresDatabase | None = None,
) -> AuthResult:
    """Validates HTTP headers present during WebSocket handshake."""
    normalized_headers = {k.lower(): v for k, v in headers.items()}

    # 1. Authorization header format
    auth_header = normalized_headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return AuthResult(
            False,
            "veetee_auth_failed",
            "Missing or invalid Authorization header format",
            None,
            None,
        )

    token = auth_header[7:].strip()
    if not token or len(token) > 4096:
        return AuthResult(
            False,
            "veetee_auth_failed",
            "Invalid authorization token length",
            None,
            None,
        )

    # 2. Protocol-Version header
    proto_ver = normalized_headers.get("protocol-version")
    if proto_ver not in ("1", "2", "3"):
        return AuthResult(
            False,
            "veetee_invalid_input",
            "Unsupported or missing Protocol-Version header (expected 1, 2, or 3)",
            None,
            None,
            1,
        )
    version = int(proto_ver)

    # 3. Device-Id header
    device_id = normalized_headers.get("device-id")
    if not device_id or not device_id.strip() or len(device_id.strip()) > id_max_length:
        return AuthResult(
            False,
            "veetee_invalid_input",
            "Missing or invalid Device-Id header",
            None,
            None,
        )
    clean_device_id = device_id.strip()

    # 4. Client-Id header
    client_id = normalized_headers.get("client-id")
    if not client_id or not client_id.strip() or len(client_id.strip()) > id_max_length:
        return AuthResult(
            False,
            "veetee_invalid_input",
            "Missing or invalid Client-Id header",
            None,
            None,
        )
    clean_client_id = client_id.strip()

    # 5. Token Verification Logic
    secret = jwt_secret

    if persistence_enabled:
        # Strict DB verification mode: NEVER fallback to shared gateway token
        if not secret or database is None:
            return AuthResult(
                False, "veetee_auth_failed", "Device authentication unavailable", None, None
            )
        jwt_payload = verify_device_ws_token(token, secret)
        if not jwt_payload:
            return AuthResult(
                False,
                "veetee_auth_failed",
                "Invalid or expired per-device WebSocket token",
                None,
                None,
            )
        if (
            jwt_payload.get("device_id") != clean_device_id
            or jwt_payload.get("client_id") != clean_client_id
        ):
            return AuthResult(
                False,
                "veetee_auth_failed",
                "WebSocket token claims do not match request headers",
                None,
                None,
            )
        # Check credential in DB
        cred_repo = DeviceCredentialRepository(database)
        if not cred_repo.verify_credential(clean_device_id, clean_client_id, token, secret):
            return AuthResult(
                False,
                "veetee_auth_failed",
                "Device credential revoked or not found in database",
                None,
                None,
            )
        # Check device status is bound
        dev_repo = DeviceRepository(database)
        dev = dev_repo.get_by_device_id(clean_device_id)
        if not dev or dev.get("status") != "bound":
            return AuthResult(
                False,
                "veetee_auth_failed",
                "Device is not bound",
                None,
                None,
            )
    else:
        # Local/Test compatibility mode: accept shared gateway token or valid per-device JWT
        is_gateway_token_valid = expected_token and secrets.compare_digest(token, expected_token)
        jwt_payload = verify_device_ws_token(token, secret) if secret else None
        is_jwt_valid = (
            jwt_payload is not None
            and jwt_payload.get("device_id") == clean_device_id
            and jwt_payload.get("client_id") == clean_client_id
        )
        if not (is_gateway_token_valid or is_jwt_valid):
            return AuthResult(
                False,
                "veetee_auth_failed",
                "Invalid authorization token",
                None,
                None,
            )

    return AuthResult(
        True,
        None,
        None,
        clean_device_id,
        clean_client_id,
        version,
    )
