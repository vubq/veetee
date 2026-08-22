"""Unit tests for M5 credential, rollout, version, and Range security rules."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from veetee_server.config import Settings
from veetee_server.device_gateway.ota_router import _single_range
from veetee_server.device_gateway.registry import DeviceSessionRegistry
from veetee_server.domain.device_lifecycle import (
    compare_semver,
    create_artifact_download_token,
    create_device_bootstrap_token,
    create_device_ws_token,
    hash_activation_code,
    is_device_in_cohort,
    verify_activation_code,
    verify_artifact_download_token,
    verify_device_bootstrap_token,
    verify_device_ws_token,
)
from veetee_server.domain.session import DeviceSession


def test_activation_hash_and_device_token_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 32
    code_hash = hash_activation_code("004209", secret, "salt")
    assert verify_activation_code("004209", code_hash, secret, "salt")
    assert not verify_activation_code("004208", code_hash, secret, "salt")

    monkeypatch.setattr("veetee_server.domain.device_lifecycle.time.time", lambda: 1000.0)
    token = create_device_ws_token("device-a", "client-a", uuid4(), 60, secret, iat=1000)
    claims = verify_device_ws_token(token, secret)
    assert claims is not None
    assert (claims["device_id"], claims["client_id"]) == ("device-a", "client-a")
    assert verify_device_ws_token(token + "x", secret) is None
    bootstrap = create_device_bootstrap_token("device-a", "client-a", uuid4(), 60, secret, iat=1000)
    assert verify_device_bootstrap_token(bootstrap, secret) is not None
    assert verify_device_ws_token(bootstrap, secret) is None
    monkeypatch.setattr("veetee_server.domain.device_lifecycle.time.time", lambda: 1060.0)
    assert verify_device_ws_token(token, secret) is None


def test_download_token_and_single_range(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_id = uuid4()
    secret = "b" * 32
    monkeypatch.setattr("veetee_server.domain.device_lifecycle.time.time", lambda: 2000.0)
    token = create_artifact_download_token(artifact_id, "device-a", 30, secret)
    assert verify_artifact_download_token(token, artifact_id, secret) == "device-a"
    assert verify_artifact_download_token(token, uuid4(), secret) is None
    assert _single_range("bytes=0-9", 100) == (0, 9)
    assert _single_range("bytes=90-", 100) == (90, 99)
    assert _single_range("bytes=-10", 100) == (90, 99)
    assert _single_range("bytes=0-1,4-5", 100) is None
    assert _single_range("bytes=100-", 100) is None


def test_m5_security_config_fails_closed() -> None:
    with pytest.raises(ValidationError, match="exactly 32 bytes"):
        Settings(ota_ed25519_public_key="ab")
    with pytest.raises(ValidationError, match="restricted to local/test"):
        Settings(environment="production", allow_insecure_activation=True)


def test_strict_semver_antirollback_and_deterministic_cohort() -> None:
    assert compare_semver("1.2.4", "1.2.3") > 0
    assert compare_semver("1.2.3", "1.2.3") == 0
    assert compare_semver("1.0.0-alpha.10", "1.0.0-alpha.2") > 0
    assert compare_semver("1.0.0-alpha", "1.0.0-1") > 0
    assert compare_semver("1.0.0", "1.0.0-rc.1") > 0
    with pytest.raises(ValueError):
        compare_semver("1.0.0-alpha.01", "1.0.0-alpha.1")
    with pytest.raises(ValueError):
        compare_semver("01.0.0", "1.0.0")
    with pytest.raises(ValueError):
        compare_semver("release-7", "1.2.3")
    rollout_id = uuid4()
    first = is_device_in_cohort("device-a", rollout_id, 37)
    assert is_device_in_cohort("device-a", rollout_id, 37) is first
    assert not is_device_in_cohort("device-a", rollout_id, 0)
    assert is_device_in_cohort("device-a", rollout_id, 100)


@pytest.mark.asyncio
async def test_device_session_registry_tracks_real_online_lifecycle() -> None:
    registry = DeviceSessionRegistry()
    session = DeviceSession(device_id="device-online", client_id="client-online")
    websocket = object()
    assert not registry.is_online("device-online")
    await registry.register(session, websocket)  # type: ignore[arg-type]
    assert registry.is_online("device-online")
    await registry.unregister(str(session.id))
    assert not registry.is_online("device-online")
