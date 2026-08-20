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
