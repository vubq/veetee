import json
import logging
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from veetee_server.app import create_app
from veetee_server.app_context import request_id_context
from veetee_server.config import Settings
from veetee_server.logging import JsonFormatter


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "test-server"}
    UUID(response.headers["X-Veetee-Request-Id"])


def test_readyz_after_lifespan(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_rejects_unavailable_native_opus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("veetee_server.app.is_native_opus_available", lambda: False)
    app = create_app(Settings(environment="test", audio_codec="native"))

    with TestClient(app) as test_client:
        response = test_client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["reason"] == "native_opus_not_ready"


def test_valid_request_id_is_preserved(client: TestClient) -> None:
    request_id = "12345678-1234-5678-1234-567812345678"

    response = client.get("/healthz", headers={"X-Veetee-Request-Id": request_id})

    assert response.headers["X-Veetee-Request-Id"] == request_id


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Veetee-Request-Id": "not-valid"})

    UUID(response.headers["X-Veetee-Request-Id"])


def test_redacted_json_log() -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, "request", (), None)
    record.context = {"api_key": "secret-value", "authorization": "Bearer abc"}
    token = request_id_context.set("12345678-1234-5678-1234-567812345678")

    try:
        output = JsonFormatter().format(record)
    finally:
        request_id_context.reset(token)

    assert "secret-value" not in output
    assert "Bearer abc" not in output
    assert json.loads(output)["context"]["api_key"] == "[REDACTED]"
    assert json.loads(output)["request_id"] == "12345678-1234-5678-1234-567812345678"


@pytest.mark.parametrize(
    "message, secret",
    [
        ("api_key=secret-value", "secret-value"),
        ("password hunter2", "hunter2"),
        ("token: tok_123", "tok_123"),
        ("Authorization: Bearer abc", "abc"),
    ],
)
def test_redacts_secret_patterns_in_log_message(message: str, secret: str) -> None:
    record = logging.LogRecord("test", logging.INFO, "", 0, message, (), None)

    output = JsonFormatter().format(record)

    assert secret not in output
    assert "[REDACTED]" in output
    assert "[REDACTED]]" not in output


def test_unhandled_exception_returns_safe_correlated_response() -> None:
    app = create_app(Settings(environment="test"))

    @app.get("/boom")
    async def boom() -> dict[str, Any]:
        raise RuntimeError("api_key=must-not-leak")

    request_id = "12345678-1234-5678-1234-567812345678"
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/boom", headers={"X-Veetee-Request-Id": request_id})

    assert response.status_code == 500
    assert response.headers["X-Veetee-Request-Id"] == request_id
    assert response.json() == {
        "code": "veetee_internal",
        "message": "Internal server error",
        "request_id": request_id,
    }
    assert "must-not-leak" not in response.text


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(log_level="NOTALEVEL")  # type: ignore[arg-type]


def test_settings_load_veetee_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEETEE_APP_NAME", "environment-server")
    monkeypatch.setenv("VEETEE_PORT", "9090")

    settings = Settings()

    assert settings.app_name == "environment-server"
    assert settings.port == 9090


def test_readiness_disabled_is_explicit() -> None:
    app = create_app(Settings(readiness_enabled=False))

    with TestClient(app) as test_client:
        response = test_client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_lifespan_resets_readiness() -> None:
    app = create_app(Settings(environment="test"))

    assert app.state.ready is False
    with TestClient(app):
        assert app.state.ready is True
    assert app.state.ready is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"audio_max_queue_duration_ms": 59.0},
        {"audio_max_queue_duration_ms": 1.0},
    ],
)
def test_audio_queue_duration_must_hold_one_frame(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"audio_pacing_max_drift_ms": 10000.0},  # equal to queue duration
        {"audio_pacing_max_drift_ms": 20000.0},  # larger than queue duration
    ],
)
def test_audio_pacing_drift_must_be_below_queue_duration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **kwargs)


def test_audio_settings_defaults_are_valid() -> None:
    settings = Settings(environment="test")
    assert settings.audio_max_queue_items == 100
    assert settings.audio_max_queue_bytes == 1048576
    assert settings.audio_max_queue_duration_ms == 10000.0
    assert settings.audio_pacing_max_drift_ms == 100.0
    assert settings.audio_pacing_max_drift_ms < settings.audio_max_queue_duration_ms


def test_audio_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VEETEE_AUDIO_MAX_QUEUE_ITEMS", "50")
    monkeypatch.setenv("VEETEE_AUDIO_MAX_QUEUE_BYTES", "65536")
    monkeypatch.setenv("VEETEE_AUDIO_MAX_QUEUE_DURATION_MS", "5000.0")
    monkeypatch.setenv("VEETEE_AUDIO_PACING_MAX_DRIFT_MS", "250.0")

    settings = Settings(environment="test")

    assert settings.audio_max_queue_items == 50
    assert settings.audio_max_queue_bytes == 65536
    assert settings.audio_max_queue_duration_ms == 5000.0
    assert settings.audio_pacing_max_drift_ms == 250.0


def test_lifecycle_secrets_use_effective_values_and_must_differ() -> None:
    with pytest.raises(ValidationError, match="activation_secret"):
        Settings(
            persistence_enabled=True,
            activation_secret="   " + "a" * 29 + "   ",
            device_jwt_secret="b" * 32,
        )
    with pytest.raises(ValidationError, match="distinct"):
        Settings(
            persistence_enabled=True,
            activation_secret="  " + "a" * 32,
            device_jwt_secret="a" * 32 + "  ",
        )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://ota.example.test",
        "https://user@ota.example.test",
        "https://ota.example.test/path",
        "https://ota.example.test?host=attacker",
        "https://ota.example.test#fragment",
    ],
)
def test_ota_public_base_url_rejects_unsafe_values(url: str) -> None:
    with pytest.raises(ValidationError, match="ota_public_base_url"):
        Settings(ota_public_base_url=url)


@pytest.mark.parametrize(
    ("ota_url", "websocket_url", "message"),
    [
        (
            "http://ota.example.test",
            "wss://device.example.test/api/v1/devices/ws",
            "ota_public_base_url must use https",
        ),
        (
            "https://ota.example.test",
            "ws://device.example.test/api/v1/devices/ws",
            "device_websocket_public_url must use wss",
        ),
    ],
)
def test_production_persistence_rejects_plaintext_public_urls(
    ota_url: str, websocket_url: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            environment="production",
            persistence_enabled=True,
            activation_secret="a" * 32,
            device_jwt_secret="b" * 32,
            ota_public_base_url=ota_url,
            device_websocket_public_url=websocket_url,
        )


@pytest.mark.parametrize("environment", ["local", "test"])
def test_local_test_persistence_explicitly_allows_plaintext_urls(environment: str) -> None:
    settings = Settings(
        environment=environment,
        persistence_enabled=True,
        activation_secret="a" * 32,
        device_jwt_secret="b" * 32,
        ota_public_base_url="http://127.0.0.1:8080",
        device_websocket_public_url="ws://127.0.0.1:8080/api/v1/devices/ws",
    )

    assert settings.ota_public_base_url.startswith("http://")
    assert settings.device_websocket_public_url.startswith("ws://")
