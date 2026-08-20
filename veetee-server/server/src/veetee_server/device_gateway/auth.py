"""Header and token authentication for Device WebSocket handshake."""

import secrets
from collections.abc import Mapping
from typing import NamedTuple


class AuthResult(NamedTuple):
    is_valid: bool
    error_code: str | None
    error_message: str | None
    device_id: str | None
    client_id: str | None


def validate_handshake_headers(
    headers: Mapping[str, str],
    expected_token: str,
    id_max_length: int = 128,
) -> AuthResult:
    """Validates HTTP headers present during WebSocket handshake."""
    # Case-insensitive header lookup helper
    normalized_headers = {k.lower(): v for k, v in headers.items()}

    # 1. Authorization header
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
    if not token or len(token) > id_max_length:
        return AuthResult(
            False,
            "veetee_auth_failed",
            "Invalid authorization token length",
            None,
            None,
        )

    if not expected_token or not secrets.compare_digest(token, expected_token):
        return AuthResult(
            False,
            "veetee_auth_failed",
            "Invalid authorization token",
            None,
            None,
        )

    # 2. Protocol-Version header
    proto_ver = normalized_headers.get("protocol-version")
    if proto_ver != "1":
        return AuthResult(
            False,
            "veetee_invalid_input",
            "Unsupported or missing Protocol-Version header (expected 1)",
            None,
            None,
        )

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

    return AuthResult(
        True,
        None,
        None,
        device_id.strip(),
        client_id.strip(),
    )
