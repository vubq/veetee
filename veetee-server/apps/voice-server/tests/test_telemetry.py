from __future__ import annotations

import asyncio
from typing import Any

import pytest

from veetee_voice_server.telemetry import ConversationTelemetryBuffer


class FakePublisher:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.attempts = 0
        self.batches: list[list[dict[str, Any]]] = []

    async def publish_conversation_events(
        self, device_id: str, events: list[dict[str, Any]]
    ) -> int:
        assert device_id == "device-1"
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            raise RuntimeError("temporary manager failure")
        self.batches.append(events)
        return len(events)


@pytest.mark.asyncio
async def test_telemetry_batches_redacts_and_flushes_on_close() -> None:
    publisher = FakePublisher()
    telemetry = ConversationTelemetryBuffer(
        publisher,
        "device-1",
        queue_capacity=8,
        batch_size=8,
        flush_seconds=1,
        shutdown_seconds=1,
    )
    telemetry.record(
        "session_12345678",
        "plan",
        generation=3,
        turn_id="session_12345678:1",
        payload={
            "intent": "device.volume",
            "text": "không được lưu transcript này",
            "nested": {"arguments": {"volume": 55}, "safe": True},
        },
    )
    telemetry.record(
        "session_12345678",
        "tts.start",
        generation=3,
        turn_id="session_12345678:1",
    )
    await telemetry.close()

    assert len(publisher.batches) == 1
    first, second = publisher.batches[0]
    assert first["sessionId"] == "session_12345678"
    assert first["turnId"] == "session_12345678:1"
    assert first["payload"] == {
        "intent": "device.volume",
        "text": "[redacted]",
        "nested": {"arguments": "[redacted]", "safe": True},
    }
    assert second["eventType"] == "tts.start"
    assert first["eventId"] != second["eventId"]
    assert first["occurredAt"].endswith("Z")


@pytest.mark.asyncio
async def test_telemetry_retries_without_blocking_the_record_path() -> None:
    publisher = FakePublisher(fail_first=True)
    telemetry = ConversationTelemetryBuffer(
        publisher,
        "device-1",
        queue_capacity=8,
        batch_size=1,
        flush_seconds=0.01,
        shutdown_seconds=1,
    )
    telemetry.record(
        "session_12345678",
        "listen.start",
        generation=1,
        payload={"source": "button"},
    )
    await asyncio.sleep(0.04)
    await telemetry.close()

    assert publisher.attempts == 2
    assert len(publisher.batches) == 1
    assert telemetry.dropped_events == 0
