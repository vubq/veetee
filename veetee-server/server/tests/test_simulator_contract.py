"""Contract and integration tests for the M1.7 Veetee device simulator."""

import asyncio
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.audio import AudioPacketMetadata, PacketPacer
from veetee_server.config import Settings
from veetee_server.simulator import (
    SimulatorConfig,
    SimulatorTransportError,
    TestClientWebSocketTransport,
    VeeteeDeviceSimulator,
    load_golden_contract,
)


@pytest.fixture
def simulator_settings() -> Settings:
    return Settings(
        app_name="simulator-test-server",
        environment="test",
        device_gateway_token="test-gateway-token",
        hello_timeout_seconds=0.2,
        idle_timeout_seconds=0.2,
        ping_interval_seconds=0.15,
        pong_timeout_seconds=0.1,
        binary_max_bytes=1024,
        audio_max_queue_items=10,
        pipeline_tts_chunks_per_sentence=3,
    )


def _headers(version: int = 1, suffix: str = "1") -> dict[str, str]:
    return {
        "Authorization": "Bearer test-gateway-token",
        "Protocol-Version": str(version),
        "Device-Id": f"sim-device-{suffix}",
        "Client-Id": f"sim-client-{suffix}",
    }


def _simulator(ws: Any, version: int = 1, suffix: str = "1") -> VeeteeDeviceSimulator:
    simulator = VeeteeDeviceSimulator(
        SimulatorConfig(
            device_id=f"sim-device-{suffix}",
            client_id=f"sim-client-{suffix}",
            token="test-gateway-token",
            protocol_version=cast(Any, version),
        )
    )
    asyncio.run(simulator.connect_ws(custom_transport=TestClientWebSocketTransport(ws)))
    return simulator


def _golden_frame(version: int) -> bytes:
    contract = load_golden_contract(f"audio_v{version}_golden.json")
    vectors = cast(list[dict[str, Any]], contract["vectors"])
    return bytes.fromhex(cast(str, vectors[0]["hex_payload"]))


def test_contract_loader_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_golden_contract("../secret.json", tmp_path)


def test_simulator_config_has_no_default_token() -> None:
    assert SimulatorConfig().token == ""


def test_ota_golden_contract(simulator_settings: Settings) -> None:
    request = load_golden_contract("ota_check_request.json")
    expected = load_golden_contract("ota_check_response.json")
    app = create_app(simulator_settings)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/devices/ota/check",
            headers={"Device-Id": "sim-device", "Client-Id": "sim-client"},
            json=request,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["websocket"]["version"] == expected["websocket"]["version"]
    assert body["firmware"] == expected["firmware"]
    assert isinstance(body["server_time"]["timestamp"], int)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_simulator_runs_complete_golden_turn(
    simulator_settings: Settings, version: int
) -> None:
    app = create_app(simulator_settings)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/devices/ws", headers=_headers(version, str(version))
        ) as ws:
            simulator = _simulator(ws, version, str(version))
            asyncio.run(simulator.send_hello())
            events = asyncio.run(simulator.run_turn(_golden_frame(version)))
            assert [event.get("type") for event in events if isinstance(event, dict)] == [
                "stt",
                "tts",
                "tts",
                "tts",
            ]
            audio = [event for event in events if isinstance(event, AudioPacketMetadata)]
            assert len(audio) == simulator_settings.pipeline_tts_chunks_per_sentence
            assert all(packet.protocol_version == version for packet in audio)
            assert asyncio.run(simulator.send_goodbye())["type"] == "goodbye"


def test_simulator_malformed_golden_vectors(simulator_settings: Settings) -> None:
    contract = load_golden_contract("audio_malformed_golden.json")
    vectors = cast(list[dict[str, Any]], contract["vectors"])
    app = create_app(simulator_settings)

    for index, vector in enumerate(vectors):
        if "max_payload_bytes" in vector:
            continue
        version = cast(int, vector.get("version", 1))
        with TestClient(app) as client:
            with client.websocket_connect(
                "/api/v1/devices/ws", headers=_headers(version, f"bad-{index}")
            ) as ws:
                simulator = _simulator(ws, version, f"bad-{index}")
                asyncio.run(simulator.send_hello())
                raw_frame = bytes.fromhex(cast(str, vector["hex_payload"]))
                asyncio.run(simulator.send_audio_frame(raw_frame))
                error = asyncio.run(simulator.receive_json())
                assert error["code"] == "veetee_invalid_input"


def test_parallel_sessions_are_isolated(simulator_settings: Settings) -> None:
    app = create_app(simulator_settings)
    with TestClient(app) as client:
        contexts = [
            client.websocket_connect("/api/v1/devices/ws", headers=_headers(1, str(index)))
            for index in range(4)
        ]
        sockets = [context.__enter__() for context in contexts]
        try:
            simulators = [_simulator(socket, 1, str(index)) for index, socket in enumerate(sockets)]
            for simulator in simulators:
                asyncio.run(simulator.send_hello())
            assert len({simulator.session_id for simulator in simulators}) == 4

            asyncio.run(simulators[0].send_abort())
            for simulator in simulators[1:]:
                asyncio.run(simulator.send_ping())
                assert asyncio.run(simulator.receive_json())["type"] == "pong"
        finally:
            for context in contexts:
                context.__exit__(None, None, None)


def test_reconnect_gets_fresh_session(simulator_settings: Settings) -> None:
    app = create_app(simulator_settings)
    session_ids: list[str | None] = []
    with TestClient(app) as client:
        for _ in range(2):
            with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
                simulator = _simulator(ws)
                asyncio.run(simulator.send_hello())
                session_ids.append(simulator.session_id)
                asyncio.run(simulator.send_goodbye())
    assert session_ids[0] != session_ids[1]


def test_reconnect_while_audio_is_streaming_cleans_old_session(
    simulator_settings: Settings,
) -> None:
    app = create_app(simulator_settings.model_copy(update={"idle_timeout_seconds": 10.0}))
    app.state.pacer_factory = lambda _settings: BlockingPacer()
    registry = app.state.device_session_registry

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            asyncio.run(simulator.send_hello())
            old_session_id = simulator.session_id
            asyncio.run(simulator.send_listen("start"))
            asyncio.run(simulator.send_audio_frame(_golden_frame(1)))
            asyncio.run(simulator.send_audio_frame(_golden_frame(1)))
            asyncio.run(simulator.send_listen("stop", None))
            assert asyncio.run(simulator.receive_json())["type"] == "stt"
            assert asyncio.run(simulator.receive_json())["state"] == "start"
            assert asyncio.run(simulator.receive_json())["state"] == "sentence_start"

        assert registry.active_count == 0

        app.state.pacer_factory = lambda settings: PacketPacer(
            max_drift_seconds=settings.audio_pacing_max_drift_ms / 1000.0
        )
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            asyncio.run(simulator.send_hello())
            assert simulator.session_id != old_session_id
            events = asyncio.run(simulator.run_turn(_golden_frame(1)))
            assert any(isinstance(event, AudioPacketMetadata) for event in events)


def test_hello_and_idle_timeouts(simulator_settings: Settings) -> None:
    app = create_app(
        simulator_settings.model_copy(
            update={"ping_interval_seconds": 1.0, "pong_timeout_seconds": 0.5}
        )
    )
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            assert asyncio.run(simulator.receive_json(timeout=1))["code"] == "veetee_timeout"

        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            asyncio.run(simulator.send_hello())
            assert asyncio.run(simulator.receive_json(timeout=1))["code"] == "veetee_timeout"


class BlockingPacer(PacketPacer):
    async def pace(self, frame_duration_seconds: float) -> float:
        del frame_duration_seconds
        await asyncio.Future()
        return 0.0


def test_slow_client_overflow_closes_1009(simulator_settings: Settings) -> None:
    settings = simulator_settings.model_copy(
        update={"audio_max_queue_items": 2, "pipeline_tts_chunks_per_sentence": 6}
    )
    app = create_app(settings)
    app.state.pacer_factory = lambda _settings: BlockingPacer()

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            asyncio.run(simulator.send_hello())
            asyncio.run(simulator.send_listen("start"))
            asyncio.run(simulator.send_audio_frame(_golden_frame(1)))
            asyncio.run(simulator.send_audio_frame(_golden_frame(1)))
            asyncio.run(simulator.send_listen("stop", None))

            with pytest.raises(SimulatorTransportError, match="1009"):
                for _ in range(10):
                    asyncio.run(simulator.receive_event())


def test_registry_shutdown_closes_active_simulator_1012(simulator_settings: Settings) -> None:
    app = create_app(simulator_settings.model_copy(update={"idle_timeout_seconds": 10.0}))
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            simulator = _simulator(ws)
            asyncio.run(simulator.send_hello())
            assert client.portal is not None
            client.portal.call(
                partial(
                    app.state.device_session_registry.close_all,
                    code=1012,
                    reason="Server shutdown",
                )
            )
            with pytest.raises(SimulatorTransportError, match="1012"):
                for _ in range(10):
                    asyncio.run(simulator.receive_event(auto_respond_ping=False))


def test_openapi_and_product_source_pass_namespace_policy(simulator_settings: Settings) -> None:
    app = create_app(simulator_settings)
    assert "xia" + "ozhi" not in str(app.openapi()).lower()
