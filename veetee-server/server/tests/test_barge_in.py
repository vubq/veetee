"""Deterministic tests for M2.6 BargeInDetector and Device Gateway speaking uplink barge-in flow."""

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from veetee_server.app import create_app
from veetee_server.audio import (
    UPLINK_PCM_FORMAT,
    AudioQueueItem,
    FakeOpusDecoder,
)
from veetee_server.config import Settings
from veetee_server.device_gateway.barge_in import (
    AutoEndpointDetector,
    BargeInCoordinator,
    BargeInDetection,
    BargeInDetector,
)
from veetee_server.domain.session import DeviceSession, SessionState
from veetee_server.pipeline.vad import VadEvent, VadEventKind


class MockVAD:
    """Controllable mock VAD stream for deterministic testing."""

    def __init__(self, events: list[VadEventKind]) -> None:
        self.events = events
        self.call_count = 0
        self.reset_called = False

    def process_frame(self, pcm_data: bytes) -> VadEvent:
        if self.call_count < len(self.events):
            kind = self.events[self.call_count]
        else:
            kind = VadEventKind.SILENCE

        event = VadEvent(kind=kind, frame_index=self.call_count)
        self.call_count += 1
        return event

    def reset(self) -> None:
        self.reset_called = True
        self.call_count = 0


@pytest.mark.asyncio
async def test_barge_in_detector_bounded_pre_roll() -> None:
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    # 5 SILENCE frames, then 1 SPEECH_START frame
    events = [VadEventKind.SILENCE] * 5 + [VadEventKind.SPEECH_START]
    vad = MockVAD(events)

    detector = BargeInDetector(decoder, vad, max_pre_roll_frames=3)

    items = [
        AudioQueueItem(payload=f"frame-{i}".encode(), duration_ms=60.0, generation=1)
        for i in range(6)
    ]

    # Process first 5 non-triggering frames
    for item in items[:5]:
        res = await detector.process(item)
        assert res is None

    # Process 6th frame which triggers speech start
    detection = await detector.process(items[5])
    assert isinstance(detection, BargeInDetection)

    # Pre-roll must be bounded to max_pre_roll_frames=3 (items[3], items[4], items[5])
    assert len(detection.frames) == 3
    assert detection.frames == (items[3], items[4], items[5])


@pytest.mark.asyncio
async def test_barge_in_detector_trigger_once() -> None:
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    # 2 SPEECH_START events in sequence
    events = [VadEventKind.SPEECH_START, VadEventKind.SPEECH_START]
    vad = MockVAD(events)
    detector = BargeInDetector(decoder, vad, max_pre_roll_frames=5)

    item1 = AudioQueueItem(payload=b"frame-1", duration_ms=60.0, generation=1)
    item2 = AudioQueueItem(payload=b"frame-2", duration_ms=60.0, generation=1)

    # First trigger succeeds
    detection1 = await detector.process(item1)
    assert detection1 is not None
    assert detection1.frames == (item1,)

    # Second process while triggered returns None (holds trigger state once)
    detection2 = await detector.process(item2)
    assert detection2 is None


@pytest.mark.asyncio
async def test_auto_endpoint_detector_triggers_once_on_speech_end() -> None:
    detector = AutoEndpointDetector(
        FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        MockVAD(
            [
                VadEventKind.SPEECH_START,
                VadEventKind.PROCESSING,
                VadEventKind.SPEECH_END,
                VadEventKind.SPEECH_END,
            ]
        ),
    )
    items = [
        AudioQueueItem(payload=f"frame-{index}".encode(), duration_ms=60.0, generation=1)
        for index in range(4)
    ]

    assert not await detector.process(items[0])
    assert detector.speech_started
    assert not await detector.process(items[1])
    assert await detector.process(items[2])
    assert not await detector.process(items[3])


@pytest.mark.asyncio
async def test_auto_endpoint_detector_ignores_speech_end_without_speech_start() -> None:
    detector = AutoEndpointDetector(
        FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        MockVAD([VadEventKind.SPEECH_END, VadEventKind.SILENCE]),
    )
    items = [
        AudioQueueItem(payload=f"frame-{index}".encode(), duration_ms=60.0, generation=1)
        for index in range(2)
    ]

    assert not await detector.process(items[0])
    assert not detector.speech_started
    assert not await detector.process(items[1])


@pytest.mark.asyncio
async def test_barge_in_detector_reset_and_isolation() -> None:
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    events = [VadEventKind.SPEECH_START, VadEventKind.SPEECH_START]
    vad = MockVAD(events)
    detector = BargeInDetector(decoder, vad, max_pre_roll_frames=5)

    item1 = AudioQueueItem(payload=b"frame-1", duration_ms=60.0, generation=1)
    detection1 = await detector.process(item1)
    assert detection1 is not None

    # Reset clears pre-roll and trigger flag, and invokes vad.reset()
    detector.reset()
    assert vad.reset_called

    # After reset, detector can trigger again cleanly
    item2 = AudioQueueItem(payload=b"frame-2", duration_ms=60.0, generation=1)
    detection2 = await detector.process(item2)
    assert detection2 is not None
    assert detection2.frames == (item2,)


@pytest.mark.asyncio
async def test_coordinator_retags_pre_roll_and_emits_one_stop() -> None:
    session = DeviceSession(device_id="device", client_id="client")
    session.negotiate_features({"aec": True})
    session.listen_mode = "realtime"
    session.accept()
    old_turn = await session.start_turn()
    session.begin_processing()
    session.begin_streaming()
    old_epoch = session.egress_queue.generation
    detector = BargeInDetector(
        FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        MockVAD([VadEventKind.SILENCE, VadEventKind.SPEECH_START]),
        max_pre_roll_frames=2,
    )
    coordinator = BargeInCoordinator(session, detector)

    first = AudioQueueItem(payload=b"first", duration_ms=60.0, generation=old_epoch)
    second = AudioQueueItem(payload=b"second", duration_ms=60.0, generation=old_epoch)
    assert not await coordinator.process(first, old_turn)
    assert await coordinator.process(second, old_turn)

    assert session.egress_queue.generation == old_epoch + 1
    assert session.egress_queue.item_count == 1
    assert [item.payload for item in session.ingress_queue.drain()] == [b"first", b"second"]


# --- Gateway speaking uplink tests ---

@pytest.fixture
def gateway_settings() -> Settings:
    return Settings(
        app_name="gateway-barge-in-test",
        environment="test",
        device_gateway_token="test-token",
        barge_in_pre_roll_frames=3,
        pipeline_vad_speech_threshold=10.0,
        pipeline_vad_start_frames=1,
    )


def _first_session(app: Any) -> DeviceSession:
    registry = app.state.device_session_registry
    sessions = list(registry._sessions.values())
    return cast(DeviceSession, sessions[0][0])


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "Protocol-Version": "1",
        "Device-Id": "device-barge-in-001",
        "Client-Id": "client-barge-in-001",
    }


def test_gateway_speaking_uplink_barge_in_when_eligible(gateway_settings: Settings) -> None:
    """When session is SPEAKING and eligible (aec=True, mode=realtime), speech triggers barge-in."""
    app = create_app(gateway_settings)
    V1_FRAME = bytes.fromhex("f8fffe0102030405")

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            # 1. Hello with AEC enabled
            ws.send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "features": {"aec": True},
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                }
            )
            session_id = ws.receive_json()["session_id"]

            # 2. Listen start with mode=realtime
            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": "realtime",
                    "session_id": session_id,
                }
            )

            # Sync point
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json() == {"type": "pong", "session_id": session_id}

            session = _first_session(app)
            assert session.is_barge_in_eligible

            # Manually transition session to SPEAKING state with active turn
            session.begin_processing()
            turn = session.current_turn
            assert turn is not None
            _ = session.begin_streaming()
            assert session.state is SessionState.SPEAKING

            # 3. Send binary audio frame during SPEAKING
            ws.send_bytes(V1_FRAME)

            # Egress sender task sends the TTS stop control payload down the websocket
            stop_msg = ws.receive_json()
            assert stop_msg == {
                "type": "tts",
                "state": "stop",
                "session_id": session_id,
            }

            # Sync point to ensure all frame processing completed
            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json() == {"type": "pong", "session_id": session_id}

            # 4. Verify Barge-In effects:
            # - Session transitioned to LISTENING
            assert cast(SessionState, session.state) is SessionState.LISTENING
            # - Current turn replaced with new turn in CAPTURING
            assert session.current_turn is not None
            assert session.current_turn is not turn

            # - Pre-roll frame retagged into ingress queue with the new generation
            ingress_items = session.ingress_queue.drain()
            assert len(ingress_items) == 1
            assert ingress_items[0].generation == session.ingress_queue.generation
            assert ingress_items[0].payload == bytes.fromhex("f8fffe0102030405")


@pytest.mark.parametrize(
    "aec_enabled, listen_mode",
    [
        (False, "realtime"),
        (True, "auto"),
        (True, "manual"),
    ],
)
def test_gateway_speaking_uplink_audio_dropped_when_ineligible(
    gateway_settings: Settings,
    aec_enabled: bool,
    listen_mode: str,
) -> None:
    """When session is SPEAKING but ineligible, uplink audio frames during SPEAKING are dropped."""
    app = create_app(gateway_settings)
    V1_FRAME = bytes.fromhex("f8fffe0102030405")

    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/devices/ws", headers=_headers()) as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "transport": "websocket",
                    "features": {"aec": aec_enabled},
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                }
            )
            session_id = ws.receive_json()["session_id"]

            ws.send_json(
                {
                    "type": "listen",
                    "state": "start",
                    "mode": listen_mode,
                    "session_id": session_id,
                }
            )

            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json() == {"type": "pong", "session_id": session_id}

            session = _first_session(app)
            assert not session.is_barge_in_eligible

            # Move session to SPEAKING
            session.begin_processing()
            turn = session.current_turn
            assert turn is not None
            session.begin_streaming()
            assert session.state is SessionState.SPEAKING

            # Send binary audio frame during SPEAKING when ineligible
            ws.send_bytes(V1_FRAME)

            ws.send_json({"type": "ping", "session_id": session_id})
            assert ws.receive_json() == {"type": "pong", "session_id": session_id}

            # Verify frame was dropped, session remains SPEAKING, no tts stop emitted
            assert session.state is SessionState.SPEAKING
            assert session.current_turn is turn
            assert session.ingress_queue.item_count == 0
            assert session.egress_queue.item_count == 0
