"""Integration tests for FastAPI readiness and pipeline with Silero VAD (M2.1)."""

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.pipeline.vad import (
    InjectedVADEngine,
    SileroVADConfig,
    SileroVADRuntime,
)


def test_readyz_with_fake_vad_default() -> None:
    settings = Settings(environment="test", device_gateway_token="test-token")
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


def test_readyz_with_silero_onnx_not_ready() -> None:
    settings = Settings(
        environment="test",
        device_gateway_token="test-token",
        vad_provider="silero_onnx",
        vad_model_path="/non_existent/model.onnx",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert resp.json()["reason"] == "vad_runtime_not_ready"


@pytest.mark.asyncio
async def test_readyz_with_silero_onnx_ready_injected() -> None:
    settings = Settings(
        environment="test",
        device_gateway_token="test-token",
        vad_provider="silero_onnx",
        vad_model_path="injected.onnx",
    )
    engine = InjectedVADEngine(handler=[0.1])
    config = SileroVADConfig()
    runtime = SileroVADRuntime(config, engine=engine)
    await runtime.startup()

    app = create_app(settings)
    app.state.vad_runtime = runtime

    with TestClient(app) as client:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    await runtime.shutdown()
