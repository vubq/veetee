"""Contract tests for the reference digital-human compatibility harness."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from veetee_server.digital_human_harness.app import (
    HarnessSettings,
    _relay_browser_to_server,
    _relay_server_to_browser,
    _TicketStore,
    create_harness_app,
    translate_browser_text,
)


@pytest.mark.parametrize("mode", ["auto", "manual", "realtime"])
def test_translate_browser_hello_and_listen_mode(mode: str) -> None:
    hello = translate_browser_text(
        json.dumps({"type": "hello", "token": "must-not-forward", "features": {"mcp": True}}),
        session_id=None,
        listen_mode=mode,  # type: ignore[arg-type]
        aec=mode == "realtime",
        max_bytes=1024,
    )
    start = translate_browser_text(
        json.dumps({"type": "listen", "state": "start", "mode": "auto"}),
        session_id="session-1",
        listen_mode=mode,  # type: ignore[arg-type]
        aec=False,
        max_bytes=1024,
    )

    assert hello == {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"aec": mode == "realtime"},
        "audio_params": {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1,
            "frame_duration": 60,
        },
    }
    assert start == {
        "type": "listen",
        "state": "start",
        "session_id": "session-1",
        "mode": mode,
    }


def test_translate_suppresses_detect_and_strips_abort_reason() -> None:
    detect = translate_browser_text(
        json.dumps({"type": "listen", "state": "detect", "text": "private speech"}),
        session_id="session-1",
        listen_mode="auto",
        aec=False,
        max_bytes=1024,
    )
    abort = translate_browser_text(
        json.dumps({"type": "abort", "reason": "wake_word_detected"}),
        session_id="session-1",
        listen_mode="auto",
        aec=False,
        max_bytes=1024,
    )

    assert detect is None
    assert abort == {"type": "abort", "session_id": "session-1"}


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        json.dumps({"type": "mcp", "payload": {}}),
        json.dumps({"type": "listen", "state": "invalid"}),
        json.dumps(["not-an-object"]),
    ],
)
def test_translate_rejects_malformed_or_unsupported_input(raw: str) -> None:
    with pytest.raises(ValueError):
        translate_browser_text(
            raw,
            session_id=None,
            listen_mode="auto",
            aec=False,
            max_bytes=1024,
        )


def test_translate_rejects_oversized_input() -> None:
    with pytest.raises(ValueError, match="size limit"):
        translate_browser_text(
            json.dumps({"type": "hello", "padding": "x" * 1024}),
            session_id=None,
            listen_mode="auto",
            aec=False,
            max_bytes=128,
        )


async def test_ticket_is_one_time_and_does_not_expose_authorization() -> None:
    store = _TicketStore(ttl_seconds=30)
    ticket = await store.issue("Bearer actual-secret")

    assert "actual-secret" not in ticket
    assert await store.redeem(f"Bearer {ticket}") == "Bearer actual-secret"
    assert await store.redeem(f"Bearer {ticket}") is None


async def test_ticket_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr("veetee_server.digital_human_harness.app.monotonic", lambda: now)
    store = _TicketStore(ttl_seconds=1)
    ticket = await store.issue("Bearer actual-secret")
    now = 102.0

    assert await store.redeem(f"Bearer {ticket}") is None


def test_harness_serves_health_and_reference_static_files() -> None:
    app = create_harness_app(HarnessSettings())

    with TestClient(app) as client:
        health = client.get("/healthz")
        upstream_health = client.get("/health")
        index = client.get("/index.html")

    assert health.json() == {
        "status": "ok",
        "service": "veetee-digital-human-harness",
    }
    assert index.status_code == 200
    assert "小智数字人页面" in index.text
    assert upstream_health.json() == {"status": "ok"}


def test_disabled_wakeword_bridge_reports_ready_without_fake_detection() -> None:
    app = create_harness_app(HarnessSettings())

    with TestClient(app) as client, client.websocket_connect("/wakeword-ws") as websocket:
        connected = websocket.receive_json()
        config = websocket.receive_json()
        websocket.send_json({"type": "start_service", "requestId": "request-1"})
        response = websocket.receive_json()

    assert connected["type"] == "bridge_connected"
    assert config["payload"] == {"enabled": False, "wakeWords": []}
    assert response == {
        "type": "start_service",
        "requestId": "request-1",
        "success": False,
        "error": "Wake-word runtime is disabled in this harness",
    }


def test_ticket_store_is_bounded() -> None:
    async def exercise() -> int:
        store = _TicketStore(ttl_seconds=30, max_items=2)
        await store.issue("Bearer one")
        await asyncio.sleep(0)
        await store.issue("Bearer two")
        await store.issue("Bearer three")
        return len(store._items)

    assert asyncio.run(exercise()) == 2


class _BrowserStub:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = iter(events)
        self.text: list[str] = []
        self.binary: list[bytes] = []

    async def receive(self) -> dict[str, object]:
        return next(self._events)

    async def send_text(self, value: str) -> None:
        self.text.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.binary.append(value)


class _ServerStub:
    def __init__(self, messages: list[str | bytes] | None = None) -> None:
        self.sent: list[str | bytes] = []
        self._messages = iter(messages or [])

    async def send(self, value: str | bytes) -> None:
        self.sent.append(value)

    def __aiter__(self) -> "_ServerStub":
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return next(self._messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


async def test_browser_relay_maps_empty_binary_to_listen_stop() -> None:
    browser = _BrowserStub(
        [
            {"type": "websocket.receive", "bytes": b"opus"},
            {"type": "websocket.receive", "bytes": b""},
            {"type": "websocket.disconnect"},
        ]
    )
    server = _ServerStub()

    await _relay_browser_to_server(
        browser,  # type: ignore[arg-type]
        server,
        HarnessSettings(),
        {"id": "session-1"},
    )

    assert server.sent[0] == b"opus"
    assert json.loads(str(server.sent[1])) == {
        "type": "listen",
        "state": "stop",
        "session_id": "session-1",
    }


async def test_server_relay_answers_ping_and_preserves_order() -> None:
    hello = json.dumps({"type": "hello", "session_id": "session-1"})
    ping = json.dumps({"type": "ping", "session_id": "session-1"})
    tts = json.dumps({"type": "tts", "state": "start", "session_id": "session-1"})
    server = _ServerStub([hello, ping, tts, b"opus"])
    browser = _BrowserStub([])
    session: dict[str, str | None] = {"id": None}

    await _relay_server_to_browser(
        browser,  # type: ignore[arg-type]
        server,
        HarnessSettings(),
        session,
    )

    assert session["id"] == "session-1"
    assert [json.loads(item)["type"] for item in browser.text] == ["hello", "tts"]
    assert browser.binary == [b"opus"]
    assert json.loads(str(server.sent[0])) == {"type": "pong", "session_id": "session-1"}
