"""Framework-independent device activation, credential, and OTA rules."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


class DeviceDomainError(Exception):
    """Base domain exception for device lifecycle operations."""


class ActivationError(DeviceDomainError):
    """Raised when device activation fails."""


class ExpiredCodeError(ActivationError):
    """Raised when an activation code challenge has expired."""


class MaxAttemptsExceededError(ActivationError):
    """Raised when maximum verification attempts are reached."""


class InvalidCodeError(ActivationError):
    """Raised when activation code does not match."""


class BindingConflictError(DeviceDomainError):
    """Raised when a device is already bound to another user."""


class IncompatibleFirmwareError(DeviceDomainError):
    """Raised when firmware release target is incompatible."""


class AntiRollbackError(DeviceDomainError):
    """Raised when target firmware version violates anti-rollback policy."""


class ReportRateLimitError(DeviceDomainError):
    """Raised when a persistent per-device report quota is exhausted."""


_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def canonical_activation_challenge(device_id: str, client_id: str, nonce: str) -> bytes:
    """Returns the versioned bytes signed by an enrolled device."""
    return f"veetee-activation-v1\n{device_id}\n{client_id}\n{nonce}\n".encode()


def verify_ed25519_proof(public_key_hex: str, proof_hex: str, message: bytes) -> bool:
    """Verifies a raw Ed25519 public key/signature using the local OpenSSL binary."""
    if not re.fullmatch(r"[0-9a-f]{64}", public_key_hex):
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{128}", proof_hex):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="veetee-activation-") as directory:
            root = Path(directory)
            key_path = root / "device-key.der"
            proof_path = root / "proof.bin"
            message_path = root / "challenge.bin"
            key_path.write_bytes(_ED25519_SPKI_PREFIX + bytes.fromhex(public_key_hex))
            proof_path.write_bytes(bytes.fromhex(proof_hex))
            message_path.write_bytes(message)
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-keyform",
                    "DER",
                    "-inkey",
                    str(key_path),
                    "-rawin",
                    "-in",
                    str(message_path),
                    "-sigfile",
                    str(proof_path),
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


def generate_activation_code() -> str:
    """Generates a secure 6-digit numeric activation code."""
    return f"{secrets.randbelow(1000000):06d}"


def hash_activation_code(code: str, secret: str, salt: str) -> str:
    """Computes HMAC-SHA256 digest for activation code."""
    key = secret.encode("utf-8")
    msg = f"{salt}:{code}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_activation_code(code: str, code_hash: str, secret: str, salt: str) -> bool:
    """Constant-time verification of activation code."""
    computed = hash_activation_code(code, secret, salt)
    return hmac.compare_digest(computed, code_hash)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _create_device_token(
    device_id: str,
    client_id: str,
    jti: str | uuid.UUID,
    ttl_seconds: int,
    secret: str,
    audience: str,
    iat: int | None = None,
) -> str:
    """Creates a compact signed HMAC-SHA256 JWT for one device credential kind."""
    now = int(iat if iat is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": "veetee-server",
        "aud": audience,
        "device_id": device_id,
        "client_id": client_id,
        "jti": str(jti),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def create_device_ws_token(
    device_id: str,
    client_id: str,
    jti: str | uuid.UUID,
    ttl_seconds: int,
    secret: str,
    iat: int | None = None,
) -> str:
    return _create_device_token(
        device_id, client_id, jti, ttl_seconds, secret, "veetee-device-ws", iat
    )


def create_device_bootstrap_token(
    device_id: str,
    client_id: str,
    jti: str | uuid.UUID,
    ttl_seconds: int,
    secret: str,
    iat: int | None = None,
) -> str:
    return _create_device_token(
        device_id, client_id, jti, ttl_seconds, secret, "veetee-device-bootstrap", iat
    )


def _verify_device_token(token: str, secret: str, audience: str) -> dict[str, Any] | None:
    """Verifies a compact token and requires its exact audience."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        header = json.loads(_b64url_decode(header_b64).decode())
        if header != {"alg": "HS256", "typ": "JWT"}:
            return None
        payload_bytes = _b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("iss") != "veetee-server" or payload.get("aud") != audience:
        return None
    now = time.time()
    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(iat, int) or iat > now + 30:
        return None
    if not isinstance(exp, int) or exp <= iat or now >= exp:
        return None
    if not all(
        isinstance(payload.get(key), str) and payload[key]
        for key in ("device_id", "client_id", "jti")
    ):
        return None
    return payload


def verify_device_ws_token(token: str, secret: str) -> dict[str, Any] | None:
    return _verify_device_token(token, secret, "veetee-device-ws")


def verify_device_bootstrap_token(token: str, secret: str) -> dict[str, Any] | None:
    return _verify_device_token(token, secret, "veetee-device-bootstrap")


def create_artifact_download_token(
    artifact_id: str | uuid.UUID,
    device_id: str,
    ttl_seconds: int,
    secret: str,
) -> str:
    """Creates a short-lived download token bound to a specific device and artifact."""
    now = int(time.time())
    payload = {
        "iss": "veetee-server",
        "aud": "veetee-ota-artifact",
        "art": str(artifact_id),
        "dev": device_id,
        "exp": now + ttl_seconds,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def verify_artifact_download_token(
    token: str,
    artifact_id: str | uuid.UUID,
    secret: str,
) -> str | None:
    """Verifies artifact download token and returns target device_id if valid."""
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload_b64, sig_b64 = parts
    expected_sig = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("iss") != "veetee-server" or payload.get("aud") != "veetee-ota-artifact":
        return None
    if payload.get("art") != str(artifact_id):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        return None
    dev = payload.get("dev")
    return str(dev) if isinstance(dev, str) else None


_SEMVER_REGEX = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def parse_semver(version: str) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    """Parses semantic version into numeric tuple and pre-release tag."""
    v_clean = version.strip()
    if not v_clean:
        return ((0, 0, 0), ())
    match = _SEMVER_REGEX.match(v_clean)
    if not match:
        raise ValueError("Firmware version must be semantic version major.minor.patch")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    prerelease = tuple((match.group("prerelease") or "").split("."))
    if prerelease == ("",):
        prerelease = ()
    if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
        raise ValueError(
            "Numeric semantic-version pre-release identifiers cannot have leading zeros"
        )
    return ((major, minor, patch), prerelease)


def compare_semver(v1: str, v2: str) -> int:
    """Compares two version strings. Returns -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2."""
    n1, pre1 = parse_semver(v1)
    n2, pre2 = parse_semver(v2)
    if n1 < n2:
        return -1
    if n1 > n2:
        return 1
    # Equal numbers: version without pre-release is greater than version with pre-release
    if not pre1 and pre2:
        return 1
    if pre1 and not pre2:
        return -1
    for left, right in zip(pre1, pre2, strict=False):
        if left == right:
            continue
        left_numeric = left.isdigit()
        right_numeric = right.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left) < int(right) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left < right else 1
    if len(pre1) != len(pre2):
        return -1 if len(pre1) < len(pre2) else 1
    return 0


def derive_activation_code(secret: str, salt: str) -> str:
    """Derives a stable six-digit physical code without storing it in plaintext."""
    digest = hmac.new(secret.encode(), f"activation:{salt}".encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def calculate_cohort_percentage(device_id: str, rollout_id: str | uuid.UUID) -> int:
    """Deterministically maps (device_id, rollout_id) to an integer 0..99."""
    key = f"{device_id}:{rollout_id}".encode()
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:8], 16) % 100


def is_device_in_cohort(
    device_id: str,
    rollout_id: str | uuid.UUID,
    cohort_percentage: int,
) -> bool:
    """Checks if a device falls into a rollout's percentage cohort."""
    if cohort_percentage >= 100:
        return True
    if cohort_percentage <= 0:
        return False
    return calculate_cohort_percentage(device_id, rollout_id) < cohort_percentage
