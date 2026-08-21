"""Tests for Gemini 3.1 native TTS streaming and key-pool fault policy."""

import asyncio
import base64
import json
import os

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from veetee_server.app import create_app
from veetee_server.config import Settings
from veetee_server.domain.session import DeviceSession
from veetee_server.pipeline.factory import build_fake_pipeline
from veetee_server.pipeline.tts import (
    GeminiKeyPool,
    GeminiTTSConfig,
    GeminiTTSRuntime,
    KeyPoolState,
    TTSAdmissionTimeoutError,
    TTSFormatError,
    TTSKeyExhaustedError,
    TTSMalformedStreamError,
    TTSProviderError,
    TTSProviderRateLimitError,
    TTSProviderUnavailableError,
    extract_pcm_from_gemini_response,
)


# --- Helper to create valid 24kHz PCM base64 SSE frame ---
def make_24k_pcm_bytes(sample_count: int = 1440) -> bytes:
    """Generates a deterministic 24kHz mono s16le PCM buffer."""
    t = np.linspace(0, sample_count / 24000.0, sample_count, endpoint=False)
    sine = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    return sine.tobytes()


def make_sse_data(pcm_bytes: bytes, mime_type: str = "audio/pcm;rate=24000") -> str:
    b64 = base64.b64encode(pcm_bytes).decode("utf-8")
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64,
                            }
                        }
                    ]
                }
            }
        ]
    }
    return f"data: {json.dumps(payload)}\n\n"


# --- Unit Tests: Extract PCM SSE ---
def test_extract_pcm_valid() -> None:
    pcm = make_24k_pcm_bytes(100)
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=24000",
                                "data": base64.b64encode(pcm).decode("utf-8"),
                            }
                        }
                    ]
                }
            }
        ]
    }
    extracted = extract_pcm_from_gemini_response(body)
    assert extracted == pcm


def test_extract_pcm_invalid_mime() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/mp3",
                                "data": "AAAA",
                            }
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(TTSFormatError, match="PCM at 24000 Hz"):
        extract_pcm_from_gemini_response(body)


def test_extract_pcm_malformed_base64() -> None:
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=24000",
                                "data": "!!!InvalidBase64!!!",
                            }
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(TTSMalformedStreamError, match="invalid base64"):
        extract_pcm_from_gemini_response(body)


def test_extract_pcm_odd_length() -> None:
    # 3 bytes is invalid for 16-bit PCM (2 bytes per sample)
    odd_b64 = base64.b64encode(b"\x01\x02\x03").decode("utf-8")
    body = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/pcm;rate=24000",
                                "data": odd_b64,
                            }
                        }
                    ]
                }
            }
        ]
    }
    with pytest.raises(TTSFormatError, match="aligned s16le"):
        extract_pcm_from_gemini_response(body)


# --- Unit Tests: Key Pool ---
def test_key_pool_least_in_flight_and_round_robin() -> None:
    pool = GeminiKeyPool(["key_a", "key_b", "key_c"])
    assert pool.total_keys == 3
    assert pool.healthy_keys_count == 3

    # Acquire 1: least in_flight is 0, picks key_0 (RR 0)
    k1 = pool.acquire_key()
    assert k1.in_flight == 1

    # Acquire 2: least in_flight is 0 (key_1 and key_2), picks key_1 (RR 1)
    k2 = pool.acquire_key()
    assert k2.in_flight == 1

    # Acquire 3: least in_flight is 0 (key_2), picks key_2
    k3 = pool.acquire_key()
    assert k3.in_flight == 1

    # All have in_flight=1. Acquire 4: RR picks next
    k4 = pool.acquire_key()
    assert k4.in_flight == 2

    # k4 may be k1 due to round-robin; release both leases held through k1.
    pool.release_key(k1.key_id)
    if k4.key_id == k1.key_id:
        pool.release_key(k4.key_id)
    assert k1.in_flight == 0

    # Acquire 5: least in_flight is k1 (0), so k1 is selected!
    k5 = pool.acquire_key()
    assert k5.key_id == k1.key_id


def test_key_pool_401_403_disable() -> None:
    pool = GeminiKeyPool(["key_a", "key_b"])
    k1 = pool.acquire_key()
    pool.record_auth_failure(k1.key_id)

    assert k1.state is KeyPoolState.DISABLED
    assert pool.healthy_keys_count == 1

    # Next acquire returns key_b
    k2 = pool.acquire_key()
    assert k2.secret_key == "key_b"

    pool.record_auth_failure(k2.key_id)
    assert pool.healthy_keys_count == 0

    with pytest.raises(TTSKeyExhaustedError):
        pool.acquire_key()


def test_key_pool_429_cooldown_and_circuit_breaker() -> None:
    pool = GeminiKeyPool(["key_a"])
    k1 = pool.acquire_key()
    pool.release_key(k1.key_id)

    pool.record_rate_limit(k1.key_id, cooldown_seconds=5.0)
    assert k1.state is KeyPoolState.COOLDOWN
    assert pool.healthy_keys_count == 0

    with pytest.raises(TTSKeyExhaustedError):
        pool.acquire_key()

    # Circuit breaker threshold test
    pool2 = GeminiKeyPool(["key_a"])
    k2 = pool2.acquire_key()
    pool2.release_key(k2.key_id)

    pool2.record_transient_failure(k2.key_id, failure_threshold=2, cooldown_seconds=10.0)
    assert k2.state is KeyPoolState.HEALTHY
    pool2.record_transient_failure(k2.key_id, failure_threshold=2, cooldown_seconds=10.0)
    assert k2.state is KeyPoolState.COOLDOWN


def test_key_pool_allows_only_one_half_open_probe() -> None:
    now = 10.0
    pool = GeminiKeyPool(["key_a"], time_func=lambda: now)
    key = pool.acquire_key()
    pool.release_key(key.key_id)
    pool.record_rate_limit(key.key_id, cooldown_seconds=5.0)

    now = 15.0
    probe = pool.acquire_key()
    with pytest.raises(TTSKeyExhaustedError):
        pool.acquire_key()

    pool.release_key(probe.key_id)
    pool.record_success(probe.key_id)
    assert pool.acquire_key().key_id == probe.key_id


# --- Mock HTTP Transport for Fault Matrix ---
class MockGeminiTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler: callable) -> None:
        self.handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.handler(request)


# --- Integration Tests: GeminiTTSAdapter Fault Matrix ---
@pytest.mark.asyncio
async def test_gemini_tts_successful_stream() -> None:
    pcm_24k = make_24k_pcm_bytes(1440)
    sse_text = make_sse_data(pcm_24k) + "data: [DONE]\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "valid_key"
        body = json.loads(request.content)
        assert body["contents"][0]["parts"][0]["text"] == "Xin chào Veetee"
        stream = httpx.ByteStream(sse_text.encode("utf-8"))
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(api_keys=["valid_key"])
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()
    chunks: list[bytes] = []
    async for chunk in adapter.synthesize("Xin chào Veetee"):
        chunks.append(chunk)

    await runtime.shutdown()
    assert len(chunks) > 0
    # Each frame is 60 ms at 24 kHz mono s16le.
    assert len(chunks[0]) == 2880


@pytest.mark.asyncio
async def test_gemini_tts_fragmented_sse_stream() -> None:
    pcm_24k = make_24k_pcm_bytes(1440)
    payload = (make_sse_data(pcm_24k) + "data: [DONE]\n\n").encode()

    class FragmentedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(payload), 7):
                yield payload[index : index + 7]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=FragmentedStream())

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(api_keys=["key1"]), http_client=client
    )
    await runtime.startup()
    chunks = [chunk async for chunk in runtime.create_adapter().synthesize("Xin chào")]
    assert [len(chunk) for chunk in chunks] == [2880]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_empty_stream_fails_over_to_second_key() -> None:
    attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["x-goog-api-key"]
        attempts.append(key)
        if key == "key1":
            return httpx.Response(200, content=b"data: [DONE]\n\n")
        data = make_sse_data(make_24k_pcm_bytes(1440)) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode())

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(api_keys=["key1", "key2"]), http_client=client
    )
    await runtime.startup()
    chunks = [chunk async for chunk in runtime.create_adapter().synthesize("Xin chào")]
    assert attempts == ["key1", "key2"]
    assert len(chunks) == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_5xx_fails_over_before_audio() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        data = make_sse_data(make_24k_pcm_bytes(1440)) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode())

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(api_keys=["key1", "key2"]), http_client=client
    )
    await runtime.startup()
    chunks = [chunk async for chunk in runtime.create_adapter().synthesize("Xin chào")]
    assert attempts == 2
    assert len(chunks) == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_401_failover_before_audio() -> None:
    key_attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["x-goog-api-key"]
        key_attempts.append(key)
        if key == "bad_key":
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        pcm_24k = make_24k_pcm_bytes(1440)
        sse_text = make_sse_data(pcm_24k) + "data: [DONE]\n\n"
        return httpx.Response(200, stream=httpx.ByteStream(sse_text.encode("utf-8")))

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(api_keys=["bad_key", "good_key"])
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()
    chunks: list[bytes] = []
    async for chunk in adapter.synthesize("Test 401 failover"):
        chunks.append(chunk)

    await runtime.shutdown()
    assert len(chunks) > 0
    assert key_attempts == ["bad_key", "good_key"]
    assert runtime.key_pool.healthy_keys_count == 1  # bad_key disabled


@pytest.mark.asyncio
async def test_gemini_tts_429_retry_after_cooldown() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "10"},
            json={"error": {"message": "Quota exceeded"}},
        )

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(api_keys=["key1"])
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()
    with pytest.raises(TTSProviderRateLimitError) as exc_info:
        async for _ in adapter.synthesize("Test 429"):
            pass

    assert exc_info.value.retry_after == 10.0
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_caps_retry_after() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "999999"})

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(api_keys=["key1"], max_retry_after_seconds=12.0),
        http_client=client,
    )
    await runtime.startup()
    with pytest.raises(TTSProviderRateLimitError):
        async for _ in runtime.create_adapter().synthesize("Xin chào"):
            pass
    entry = runtime.key_pool._entries[0]
    remaining = entry.cooldown_until - runtime.key_pool._time()
    assert 0 < remaining <= 12.0
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_no_replay_after_first_pcm() -> None:
    """Disconnect/error AFTER first audio chunk must NOT failover or replay audio."""
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        pcm_24k = make_24k_pcm_bytes(1440)
        # Yield first audio frame, then simulate broken SSE stream error
        sse_text = make_sse_data(pcm_24k) + (
            'data: {"error": {"code": 500, "message": "Mid-stream drop"}}\n\n'
        )
        return httpx.Response(200, stream=httpx.ByteStream(sse_text.encode("utf-8")))

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(api_keys=["key1", "key2"])
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()
    received_chunks = 0
    with pytest.raises(TTSProviderError, match="stream failed"):
        async for _chunk in adapter.synthesize("Test mid-stream error"):
            received_chunks += 1

    await runtime.shutdown()
    assert received_chunks >= 1
    # Crucial: exactly 1 request made, NO second request/replay occurred!
    assert request_count == 1


@pytest.mark.asyncio
async def test_gemini_tts_fallback_model_policy() -> None:
    model_attempts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "streamGenerateContent" in url_str:
            model_attempts.append("3.1")
            return httpx.Response(500, json={"error": "Main model error"})
        model_attempts.append("2.5")
        pcm_24k = make_24k_pcm_bytes(1440)
        payload = make_sse_data(pcm_24k)[6:].strip()
        return httpx.Response(200, content=payload.encode("utf-8"))

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    # Fallback model disabled by default
    cfg_no_fb = GeminiTTSConfig(api_keys=["key1"], enable_fallback_model=False)
    rt_no_fb = GeminiTTSRuntime(config=cfg_no_fb, http_client=client)
    await rt_no_fb.startup()

    adapter_no_fb = rt_no_fb.create_adapter()
    with pytest.raises(TTSProviderUnavailableError):
        async for _ in adapter_no_fb.synthesize("Hello"):
            pass
    assert model_attempts == ["3.1"]

    # Fallback model explicitly enabled
    model_attempts.clear()
    cfg_fb = GeminiTTSConfig(api_keys=["key1"], enable_fallback_model=True)
    rt_fb = GeminiTTSRuntime(config=cfg_fb, http_client=client)
    await rt_fb.startup()

    adapter_fb = rt_fb.create_adapter()
    chunks: list[bytes] = []
    async for chunk in adapter_fb.synthesize("Hello"):
        chunks.append(chunk)

    assert model_attempts == ["3.1", "2.5"]
    assert len(chunks) > 0

    await rt_no_fb.shutdown()
    await rt_fb.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_buffered_fallback_enforces_size_limit() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if "streamGenerateContent" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, content=b"{" + b"x" * 128 + b"}")

    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(
            api_keys=["key1"],
            enable_fallback_model=True,
            max_response_bytes=64,
        ),
        http_client=httpx.AsyncClient(transport=MockGeminiTransport(handler)),
    )
    await runtime.startup()
    with pytest.raises(TTSMalformedStreamError, match="size limit"):
        async for _ in runtime.create_adapter().synthesize("Xin chào"):
            pass
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_concurrency_admission_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        pcm_24k = make_24k_pcm_bytes(1440)
        sse_text = make_sse_data(pcm_24k) + "data: [DONE]\n\n"
        return httpx.Response(200, stream=httpx.ByteStream(sse_text.encode("utf-8")))

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(
        api_keys=["key1"],
        max_concurrency=1,
        admission_timeout_seconds=0.1,
    )
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()

    async def worker() -> None:
        async for _ in adapter.synthesize("Long request"):
            pass

    task1 = asyncio.create_task(worker())
    await asyncio.sleep(0.02)

    # Second worker should hit admission timeout
    with pytest.raises(TTSAdmissionTimeoutError):
        async for _ in adapter.synthesize("Blocked request"):
            pass

    await task1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_gemini_tts_cancellation_decrements_in_flight() -> None:
    started_evt = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started_evt.set()
        await asyncio.sleep(10.0)  # Hang request to be cancelled
        return httpx.Response(200, content=b"")

    client = httpx.AsyncClient(transport=MockGeminiTransport(handler))
    cfg = GeminiTTSConfig(api_keys=["key1"])
    runtime = GeminiTTSRuntime(config=cfg, http_client=client)
    await runtime.startup()

    adapter = runtime.create_adapter()

    async def run_synth() -> None:
        async for _ in adapter.synthesize("Cancel me"):
            pass

    task = asyncio.create_task(run_synth())
    await started_evt.wait()

    # Check key is in_flight=1
    key_entry = runtime.key_pool._entries[0]
    assert key_entry.in_flight == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Check key in_flight decremented back to 0
    assert key_entry.in_flight == 0
    await runtime.shutdown()


def test_health_readiness_integration() -> None:
    settings = Settings(environment="test", tts_provider="gemini", tts_gemini_api_keys=["k1"])
    app = create_app(settings)
    client = TestClient(app)

    with client:
        # Client context triggers lifespan startup
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


def test_settings_parse_comma_separated_keys_without_duplicates() -> None:
    previous = os.environ.get("VEETEE_TTS_GEMINI_API_KEYS")
    os.environ["VEETEE_TTS_GEMINI_API_KEYS"] = "key1,key2,key1"
    try:
        settings = Settings()
    finally:
        if previous is None:
            os.environ.pop("VEETEE_TTS_GEMINI_API_KEYS", None)
        else:
            os.environ["VEETEE_TTS_GEMINI_API_KEYS"] = previous
    assert settings.tts_gemini_api_keys == ["key1", "key2"]


def test_settings_require_keys_and_valid_tts_deadlines() -> None:
    with pytest.raises(ValidationError, match="tts_gemini_api_keys"):
        Settings(tts_provider="gemini", tts_gemini_api_keys=[])
    with pytest.raises(ValidationError, match="tts_total_timeout_seconds"):
        Settings(
            tts_total_timeout_seconds=2.0,
            tts_connect_timeout_seconds=3.0,
        )


@pytest.mark.asyncio
async def test_factory_uses_gemini_24k_pcm_encoder_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        data = make_sse_data(make_24k_pcm_bytes(1440)) + "data: [DONE]\n\n"
        return httpx.Response(200, content=data.encode())

    runtime = GeminiTTSRuntime(
        GeminiTTSConfig(api_keys=["key1"]),
        http_client=httpx.AsyncClient(transport=MockGeminiTransport(handler)),
    )
    await runtime.startup()
    session = DeviceSession(device_id="device", client_id="client")
    pipeline = build_fake_pipeline(
        session,
        Settings(environment="test", tts_provider="gemini", tts_gemini_api_keys=["key1"]),
        tts_runtime=runtime,
    )

    chunks = [chunk async for chunk in pipeline.tts.synthesize("Xin chào")]
    assert len(chunks[0]) == pipeline.encoder.pcm_format.expected_bytes == 2880
    await runtime.shutdown()
