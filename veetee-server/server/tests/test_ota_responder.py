"""Comprehensive tests for Veetee OTA/Config Responder (M1.4)."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings, validate_device_websocket_url
from veetee_server.device_gateway.ota import ota_check


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_name="ota-test-server",
        environment="test",
        host="127.0.0.1",
        port=8080,
        device_gateway_token="secret-gateway-token",
        json_max_bytes=512,
        json_max_depth=4,
        id_max_length=64,
    )


@pytest.fixture
def valid_headers() -> dict[str, str]:
    return {
        "Device-Id": "test-device-001",
        "Client-Id": "test-client-001",
        "User-Agent": "VeeteeFirmware/1.0",
        "Accept-Language": "vi-VN",
    }


@pytest.fixture
def valid_system_info() -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "board": "esp32-s3-box",
        "chip_model_name": "esp32s3",
        "minimum_free_heap_size": 120000,
    }


def test_ota_check_post_happy_path(
    test_settings: Settings,
    valid_headers: dict[str, str],
    valid_system_info: dict[str, Any],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers=valid_headers,
            json=valid_system_info,
        )

        assert resp.status_code == 200
        req_id = resp.headers.get("X-Veetee-Request-Id")
        assert req_id is not None
        UUID(req_id)

        data = resp.json()
        assert "server_time" in data
        assert isinstance(data["server_time"]["timestamp"], int)
        assert isinstance(data["server_time"]["timezone_offset"], int)

        # Baseline firmware parses server_time.timestamp as epoch MILLISECONDS
        # (ota.cc: ts/1000 for tv_sec). It must track current time, not a fixed
        # or second-based value, otherwise the device clock would be set to ~1970.
        assert abs(data["server_time"]["timestamp"] - int(time.time() * 1000)) < 5000

        assert "websocket" in data
        assert data["websocket"]["url"] == "ws://127.0.0.1:8080/api/v1/devices/ws"
        assert data["websocket"]["token"] == "secret-gateway-token"
        assert data["websocket"]["version"] == 1

        assert "firmware" in data
        assert data["firmware"] == {"version": "", "url": ""}

        # Negative assertions for absent/unsupported fields
        assert "mqtt" not in data
        assert "activation" not in data

        # Namespace check (forbidden name built dynamically so the product
        # source tree stays clean for the namespace scanner)
        forbidden = "xiao" + "zhi"
        raw_json = json.dumps(data)
        assert forbidden not in raw_json.lower()


def test_ota_check_get_and_empty_post_happy_path(
    test_settings: Settings,
    valid_headers: dict[str, str],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # GET request
        resp_get = client.get("/api/v1/devices/ota/check", headers=valid_headers)
        assert resp_get.status_code == 200
        assert resp_get.json()["websocket"]["token"] == "secret-gateway-token"

        # Empty body POST
        resp_post = client.post("/api/v1/devices/ota/check", headers=valid_headers)
        assert resp_post.status_code == 200
        assert resp_post.json()["websocket"]["token"] == "secret-gateway-token"


def test_ota_check_options_cors(test_settings: Settings) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        resp = client.options("/api/v1/devices/ota/check")
        assert resp.status_code == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
        assert "Access-Control-Allow-Credentials" not in resp.headers


@pytest.mark.parametrize(
    "mod_headers, expected_msg",
    [
        ({"Device-Id": ""}, "Missing or invalid Device-Id header"),
        ({"Client-Id": ""}, "Missing or invalid Client-Id header"),
        ({"Device-Id": "x" * 65}, "Missing or invalid Device-Id header"),
        ({"User-Agent": "u" * 257}, "User-Agent header exceeds maximum length"),
        ({"Accept-Language": "l" * 129}, "Accept-Language header exceeds maximum length"),
    ],
)
def test_invalid_headers_return_safe_error(
    test_settings: Settings,
    valid_headers: dict[str, str],
    mod_headers: dict[str, str],
    expected_msg: str,
) -> None:
    app = create_app(test_settings)
    headers = {**valid_headers, **mod_headers}
    if mod_headers.get("Device-Id") == "":
        del headers["Device-Id"]
    if mod_headers.get("Client-Id") == "":
        del headers["Client-Id"]

    with TestClient(app) as client:
        resp = client.post("/api/v1/devices/ota/check", headers=headers, json={"a": 1})
        assert resp.status_code == 400
        err = resp.json()
        assert err["code"] == "veetee_invalid_input"
        assert err["message"] == expected_msg
        assert "request_id" in err


def test_wrong_content_type(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "text/plain"},
            content="version=1.0.0",
        )
        assert resp.status_code == 415
        err = resp.json()
        assert err["code"] == "veetee_invalid_input"
        assert err["message"] == "Content-Type must be application/json"


def test_oversized_payload(test_settings: Settings, valid_headers: dict[str, str]) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        large_body = json.dumps({"padding": "a" * 600})
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content=large_body,
        )
        assert resp.status_code == 413
        err = resp.json()
        assert err["code"] == "veetee_payload_too_large"
        assert "payload size limit" in err["message"]


def test_malformed_json_and_depth_and_non_object(
    test_settings: Settings,
    valid_headers: dict[str, str],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # Malformed syntax
        resp1 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content="{invalid_json",
        )
        assert resp1.status_code == 400
        assert resp1.json()["code"] == "veetee_invalid_input"

        # Exceeds max depth (max_depth=4 in test_settings)
        deep_body = json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}})
        resp2 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content=deep_body,
        )
        assert resp2.status_code == 400
        assert resp2.json()["code"] == "veetee_invalid_input"

        # Non-object JSON (array)
        resp3 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content="[1, 2, 3]",
        )
        assert resp3.status_code == 400
        err3 = resp3.json()
        assert err3["code"] == "veetee_invalid_input"
        assert err3["message"] == "JSON body must be an object"


def test_dict_bounds_violations(
    test_settings: Settings,
    valid_headers: dict[str, str],
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        # Key too long
        long_key_body = json.dumps({"k" * 65: "value"})
        resp1 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content=long_key_body,
        )
        assert resp1.status_code == 400
        assert "Payload key exceeds maximum length" in resp1.json()["message"]

        # String value too long
        long_val_body = json.dumps({"key": "v" * 257})
        resp2 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content=long_val_body,
        )
        assert resp2.status_code == 400
        assert "Payload string value exceeds maximum length" in resp2.json()["message"]

        # Too many keys
        too_many_keys = {f"k_{i}": i for i in range(35)}
        resp3 = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            json=too_many_keys,
        )
        assert resp3.status_code == 400
        assert "Payload contains too many fields" in resp3.json()["message"]


def test_no_update_firmware_is_never_triggered(
    test_settings: Settings,
    valid_headers: dict[str, str],
) -> None:
    """Empty version/url must never be interpreted as a firmware update.

    Baseline firmware only upgrades when both ``version`` and ``url`` are
    strings AND ``IsNewVersionAvailable(current, new)`` is true; an empty new
    version parses to no components, so it can never be greater than the
    device's current version. The request body must not influence eligibility
    in M1.4.
    """
    app = create_app(test_settings)
    body = {
        "version": "999.999.999",
        "application": {"version": "999.999.999"},
        "firmware": {"version": "999.999.999", "url": "http://evil.invalid/fw.bin"},
    }
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            json=body,
        )
        assert resp.status_code == 200
        assert resp.json()["firmware"] == {"version": "", "url": ""}


def test_ota_response_does_not_log_token(
    test_settings: Settings,
    valid_headers: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gateway token must only appear in the intended OTA response, never in logs."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        resp = client.get("/api/v1/devices/ota/check", headers=valid_headers)
        assert resp.status_code == 200
        assert resp.json()["websocket"]["token"] == "secret-gateway-token"

    captured = capsys.readouterr()
    assert "secret-gateway-token" not in captured.err
    assert "secret-gateway-token" not in captured.out


def test_body_requires_json_content_type(
    test_settings: Settings, valid_headers: dict[str, str]
) -> None:
    """A non-empty body without application/json content type is rejected (415)."""
    app = create_app(test_settings)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/x-www-form-urlencoded"},
            content="a=1",
        )
        assert resp.status_code == 415
        assert resp.json()["code"] == "veetee_invalid_input"


def test_url_validation_helper() -> None:
    # Valid
    ok1, err1 = validate_device_websocket_url("ws://localhost:8080/api/v1/devices/ws")
    assert ok1 is True
    assert err1 is None

    ok2, err2 = validate_device_websocket_url("wss://example.com/api/v1/devices/ws")
    assert ok2 is True
    assert err2 is None

    # Invalid scheme
    ok3, err3 = validate_device_websocket_url("http://example.com/ws")
    assert ok3 is False
    assert err3 == "Invalid scheme: must be ws or wss"

    # Userinfo / credentials
    ok4, err4 = validate_device_websocket_url("ws://admin:secret@example.com/ws")
    assert ok4 is False
    assert err4 == "URL must not contain userinfo or credentials"

    # Fragment
    ok5, err5 = validate_device_websocket_url("ws://example.com/ws#section")
    assert ok5 is False
    assert err5 == "URL must not contain fragment"

    # Query string (no query params are defined for the Veetee WS endpoint)
    ok6, err6 = validate_device_websocket_url("ws://example.com/ws?from=mqtt_gateway")
    assert ok6 is False
    assert err6 == "URL must not contain query string"

    # Malformed / out-of-range port
    ok7, err7 = validate_device_websocket_url("ws://example.com:99999/ws")
    assert ok7 is False
    assert err7 == "URL port must be between 1 and 65535"

    ok8, err8 = validate_device_websocket_url("ws://example.com:abc/ws")
    assert ok8 is False
    assert "Invalid URL host or port" in (err8 or "")

    # Missing host
    ok9, err9 = validate_device_websocket_url("ws:///nohost")
    assert ok9 is False
    assert err9 == "URL must contain a valid host"

    # Empty value
    ok10, err10 = validate_device_websocket_url("")
    assert ok10 is False
    assert err10 == "URL must be a non-empty string"

    # Public discovery must point to the canonical Veetee WebSocket path.
    ok11, err11 = validate_device_websocket_url("wss://example.com/ws")
    assert ok11 is False
    assert err11 == "URL path must be /api/v1/devices/ws"


def test_nonstandard_json_constants_are_rejected(
    test_settings: Settings, valid_headers: dict[str, str]
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/devices/ota/check",
            headers={**valid_headers, "Content-Type": "application/json"},
            content='{"temperature": NaN}',
        )

    assert response.status_code == 400
    assert response.json()["code"] == "veetee_invalid_input"
    assert "NaN" not in response.text


def test_settings_rejects_invalid_websocket_url() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(environment="test", device_websocket_public_url="http://bad/ws")
    with pytest.raises(ValidationError):
        Settings(environment="test", device_websocket_public_url="wss://user@host/ws")


def test_custom_websocket_public_url(valid_headers: dict[str, str]) -> None:
    settings = Settings(
        environment="test",
        device_gateway_token="secret-token",
        device_websocket_public_url="wss://device.veetee.internal/api/v1/devices/ws",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/api/v1/devices/ota/check", headers=valid_headers)
        assert resp.status_code == 200
        assert resp.json()["websocket"]["url"] == "wss://device.veetee.internal/api/v1/devices/ws"


def test_production_readiness_checks() -> None:
    # 1. Missing gateway token -> 503
    app_no_token = create_app(
        Settings(
            environment="production",
            device_gateway_token="",
        )
    )
    with TestClient(app_no_token) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["reason"] == "gateway_token_not_configured"

    # 2. Production env with explicit wss:// URL and token -> 200 ready
    app_prod_secure = create_app(
        Settings(
            environment="production",
            device_gateway_token="prod-token",
            device_websocket_public_url="wss://api.veetee.ai/api/v1/devices/ws",
        )
    )
    with TestClient(app_prod_secure) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


@pytest.mark.parametrize(
    ("ota_url", "websocket_url", "reason"),
    [
        (
            "http://ota.example.test",
            "wss://api.veetee.ai/api/v1/devices/ws",
            "insecure_ota_public_url",
        ),
        (
            "https://ota.example.test",
            "ws://api.veetee.ai/api/v1/devices/ws",
            "insecure_websocket_public_url",
        ),
    ],
)
def test_production_persistence_readiness_rejects_plaintext_if_validation_is_bypassed(
    tmp_path: Path,
    ota_url: str,
    websocket_url: str,
    reason: str,
) -> None:
    settings = Settings(
        environment="test",
        persistence_enabled=True,
        activation_secret="a" * 32,
        device_jwt_secret="b" * 32,
        ota_artifact_dir=str(tmp_path),
        ota_public_base_url=ota_url,
        device_websocket_public_url=websocket_url,
        ota_ed25519_public_key="1" * 64,
    ).model_copy(update={"environment": "production"})
    app = create_app(settings)
    app.state.ready = True
    app.state.database = object()
    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/readyz"
    )

    response = asyncio.run(endpoint())

    assert response.status_code == 503
    assert json.loads(response.body)["reason"] == reason


def test_production_persistence_discovery_rejects_plaintext_transport() -> None:
    settings = Settings(
        environment="test",
        persistence_enabled=True,
        activation_secret="a" * 32,
        device_jwt_secret="b" * 32,
    ).model_copy(update={"environment": "production"})
    app = create_app(settings)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/devices/ota/check",
            "raw_path": b"/api/v1/devices/ota/check",
            "query_string": b"",
            "headers": [(b"device-id", b"device-a"), (b"client-id", b"client-a")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "app": app,
        },
        receive,
    )

    response = asyncio.run(ota_check(request))

    assert response.status_code == 400
    assert json.loads(response.body)["message"] == "Production device discovery requires HTTPS"


def test_golden_fixtures_parsing(valid_headers: dict[str, str]) -> None:
    contracts_dir = Path(__file__).resolve().parents[2] / "contracts" / "device"
    req_file = contracts_dir / "ota_check_request.json"
    res_file = contracts_dir / "ota_check_response.json"

    assert req_file.exists()
    assert res_file.exists()

    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    res_data = json.loads(res_file.read_text(encoding="utf-8"))

    settings = Settings(environment="test", device_gateway_token="test-gateway-token")
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/devices/ota/check",
            headers=valid_headers,
            json=req_data,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["websocket"] == res_data["websocket"]
        assert data["firmware"] == res_data["firmware"]

        # server_time is time-dependent, so only the schema is compared against
        # the golden fixture (timestamp is epoch milliseconds, offset in minutes).
        st = data["server_time"]
        assert set(st.keys()) == {"timestamp", "timezone_offset"}
        assert isinstance(st["timestamp"], int) and st["timestamp"] > 1_000_000_000_000
        assert isinstance(st["timezone_offset"], int)
        assert st.keys() == res_data["server_time"].keys()

        # Golden fixture must not contain usable credentials or upstream branding.
        forbidden = "xiao" + "zhi"
        fixture_text = res_file.read_text(encoding="utf-8")
        assert forbidden not in fixture_text.lower()
        assert res_data["websocket"]["token"] == "test-gateway-token"
