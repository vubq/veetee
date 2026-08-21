"""Native Gemini TTS adapter with bounded failover and key-pool lifecycle."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from veetee_server.pipeline.llm.errors import (
    LLMMalformedStreamError,
    LLMOversizedStreamError,
)
from veetee_server.pipeline.llm.omniroute import SSEDecoder

from .contract import GeminiTTSConfig
from .errors import (
    TTSAdmissionTimeoutError,
    TTSConnectTimeoutError,
    TTSFirstAudioTimeoutError,
    TTSFormatError,
    TTSKeyExhaustedError,
    TTSMalformedStreamError,
    TTSNotReadyError,
    TTSProviderAuthError,
    TTSProviderError,
    TTSProviderRateLimitError,
    TTSProviderUnavailableError,
    TTSTotalTimeoutError,
)
from .key_pool import GeminiKeyPool, KeyEntry

logger = logging.getLogger("veetee.tts.gemini")
_FRAME_BYTES_24K = 2_880


def extract_pcm_from_gemini_response(body: dict[str, Any]) -> bytes | None:
    """Extract one strict 24 kHz mono s16le PCM payload."""
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return None
    for part in parts:
        inline = part.get("inlineData") if isinstance(part, dict) else None
        if inline is None and isinstance(part, dict):
            inline = part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        mime = inline.get("mimeType", inline.get("mime_type"))
        if not isinstance(mime, str):
            raise TTSFormatError("Gemini audio is missing mimeType")
        normalized_mime = mime.lower().replace(" ", "")
        is_pcm = normalized_mime.startswith(("audio/pcm", "audio/l16"))
        is_mono = "channels=" not in normalized_mime or "channels=1" in normalized_mime
        if not is_pcm or "rate=24000" not in normalized_mime or not is_mono:
            raise TTSFormatError("Gemini audio must be mono PCM at 24000 Hz")
        encoded = inline.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise TTSMalformedStreamError("Gemini audio is missing base64 data")
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TTSMalformedStreamError("Gemini audio contains invalid base64") from exc
        if not pcm or len(pcm) % 2:
            raise TTSFormatError("Gemini PCM must contain aligned s16le samples")
        return pcm
    return None


class GeminiTTSRuntime:
    """Application-scoped HTTP client, key pool, and global limiter."""

    def __init__(
        self,
        config: GeminiTTSConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._external_client = http_client is not None
        self._client = http_client
        self.key_pool = GeminiKeyPool(config.api_keys)
        self.semaphore = asyncio.Semaphore(config.max_concurrency)
        self._started = False

    @property
    def is_ready(self) -> bool:
        return (
            self._started
            and self._client is not None
            and self.key_pool.healthy_keys_count > 0
        )

    async def startup(self) -> None:
        if self._started:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=None)
        self._started = True
        logger.info("gemini_tts_runtime_started", extra={"key_count": self.key_pool.total_keys})

    async def shutdown(self) -> None:
        self._started = False
        if self._client is not None and not self._external_client:
            await self._client.aclose()
        self._client = None

    def create_adapter(self) -> GeminiTTSAdapter:
        if not self.is_ready or self._client is None:
            raise TTSNotReadyError("Gemini TTS runtime is not ready")
        return GeminiTTSAdapter(self.config, self._client, self.key_pool, self.semaphore)


class GeminiTTSAdapter:
    """Streams Gemini PCM and retries only before the first emitted frame."""

    def __init__(
        self,
        config: GeminiTTSConfig,
        client: httpx.AsyncClient,
        key_pool: GeminiKeyPool,
        semaphore: asyncio.Semaphore,
    ) -> None:
        self.config = config
        self._client = client
        self.key_pool = key_pool
        self.semaphore = semaphore

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        if not sentence.strip():
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.total_timeout_seconds
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(),
                min(self.config.admission_timeout_seconds, self.config.total_timeout_seconds),
            )
        except TimeoutError as exc:
            if loop.time() >= deadline:
                raise TTSTotalTimeoutError("TTS total timeout exceeded") from exc
            raise TTSAdmissionTimeoutError("TTS admission timeout exceeded") from exc

        emitted = False
        last_error: Exception | None = None
        try:
            models = [(self.config.main_model, True)]
            if self.config.enable_fallback_model:
                models.append((self.config.fallback_model, False))
            for model, streaming in models:
                excluded: set[str] = set()
                while len(excluded) < self.key_pool.total_keys:
                    entry: KeyEntry | None = None
                    try:
                        entry = self.key_pool.acquire_key(excluded)
                        excluded.add(entry.key_id)
                        async for chunk in self._request(
                            sentence, model, streaming, entry, deadline
                        ):
                            emitted = True
                            yield chunk
                        self.key_pool.record_success(entry.key_id)
                        return
                    except TTSProviderAuthError as exc:
                        last_error = exc
                        if entry is not None:
                            self.key_pool.record_auth_failure(entry.key_id)
                    except TTSProviderRateLimitError as exc:
                        last_error = exc
                        if entry is not None:
                            retry = exc.retry_after or self.config.circuit_breaker_cooldown_seconds
                            self.key_pool.record_rate_limit(
                                entry.key_id, min(retry, self.config.max_retry_after_seconds)
                            )
                    except (
                        TTSConnectTimeoutError,
                        TTSFirstAudioTimeoutError,
                        TTSProviderUnavailableError,
                        TTSMalformedStreamError,
                        TTSFormatError,
                    ) as exc:
                        last_error = exc
                        if entry is not None:
                            self.key_pool.record_transient_failure(
                                entry.key_id,
                                self.config.circuit_breaker_failure_threshold,
                                self.config.circuit_breaker_cooldown_seconds,
                            )
                    except TTSKeyExhaustedError as exc:
                        last_error = exc
                        break
                    finally:
                        if entry is not None:
                            self.key_pool.release_key(entry.key_id)
                    if emitted:
                        assert last_error is not None
                        raise last_error
            if last_error is not None:
                raise last_error
            raise TTSKeyExhaustedError("No Gemini TTS key is available")
        finally:
            self.semaphore.release()

    async def _request(
        self,
        sentence: str,
        model: str,
        streaming: bool,
        entry: KeyEntry,
        deadline: float,
    ) -> AsyncIterator[bytes]:
        method = "streamGenerateContent?alt=sse" if streaming else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"
        text = f"{self.config.prompt_prefix}{sentence}"
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.config.voice}}
                },
            },
        }
        request = self._client.build_request(
            "POST",
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": entry.secret_key},
            json=payload,
        )
        loop = asyncio.get_running_loop()
        connect_budget = min(self.config.connect_timeout_seconds, deadline - loop.time())
        if connect_budget <= 0:
            raise TTSTotalTimeoutError("TTS total timeout exceeded")
        try:
            response = await asyncio.wait_for(
                self._client.send(request, stream=True), connect_budget
            )
        except TimeoutError as exc:
            if loop.time() >= deadline:
                raise TTSTotalTimeoutError("TTS total timeout exceeded") from exc
            raise TTSConnectTimeoutError("Could not connect to Gemini TTS") from exc
        except httpx.TransportError as exc:
            raise TTSProviderUnavailableError("Gemini TTS transport failed") from exc

        try:
            self._raise_for_status(response)
            if streaming:
                source = self._streaming_pcm(response, deadline)
            else:
                source = self._buffered_pcm(response, deadline)
            buffer = bytearray()
            saw_audio = False
            async for pcm in source:
                saw_audio = True
                buffer.extend(pcm)
                while len(buffer) >= _FRAME_BYTES_24K:
                    yield bytes(buffer[:_FRAME_BYTES_24K])
                    del buffer[:_FRAME_BYTES_24K]
            if not saw_audio:
                raise TTSMalformedStreamError("Gemini TTS returned no audio")
            if buffer:
                buffer.extend(b"\x00" * (_FRAME_BYTES_24K - len(buffer)))
                yield bytes(buffer)
        finally:
            await response.aclose()

    async def _streaming_pcm(
        self, response: httpx.Response, deadline: float
    ) -> AsyncIterator[bytes]:
        decoder = SSEDecoder(self.config.max_response_bytes)
        iterator = response.aiter_bytes().__aiter__()
        loop = asyncio.get_running_loop()
        first_deadline = loop.time() + self.config.first_audio_timeout_seconds
        saw_audio = False
        done = False
        while not done:
            now = loop.time()
            budget = deadline - now
            if not saw_audio:
                budget = min(budget, first_deadline - now)
            if budget <= 0:
                if saw_audio or now >= deadline:
                    raise TTSTotalTimeoutError("TTS total timeout exceeded")
                raise TTSFirstAudioTimeoutError("TTS first-audio timeout exceeded")
            try:
                raw = await asyncio.wait_for(anext(iterator), budget)
                events = decoder.feed_bytes(raw)
            except StopAsyncIteration:
                try:
                    events = decoder.finish()
                except (LLMMalformedStreamError, LLMOversizedStreamError) as exc:
                    raise TTSMalformedStreamError("Malformed Gemini SSE stream") from exc
                done = True
            except TimeoutError as exc:
                if saw_audio or loop.time() >= deadline:
                    raise TTSTotalTimeoutError("TTS total timeout exceeded") from exc
                raise TTSFirstAudioTimeoutError("TTS first-audio timeout exceeded") from exc
            except httpx.TransportError as exc:
                raise TTSProviderUnavailableError("Gemini TTS stream was interrupted") from exc
            except (LLMMalformedStreamError, LLMOversizedStreamError) as exc:
                raise TTSMalformedStreamError("Malformed Gemini SSE stream") from exc
            for data in events:
                if data == "[DONE]":
                    done = True
                    continue
                body = self._parse_body(data)
                pcm = extract_pcm_from_gemini_response(body)
                if pcm is not None:
                    saw_audio = True
                    yield pcm

    async def _buffered_pcm(
        self, response: httpx.Response, deadline: float
    ) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        chunks = bytearray()
        iterator = response.aiter_bytes().__aiter__()
        while True:
            budget = deadline - loop.time()
            if budget <= 0:
                raise TTSTotalTimeoutError("TTS total timeout exceeded")
            try:
                chunk = await asyncio.wait_for(anext(iterator), budget)
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                raise TTSTotalTimeoutError("TTS total timeout exceeded") from exc
            except httpx.TransportError as exc:
                raise TTSProviderUnavailableError(
                    "Gemini TTS buffered response was interrupted"
                ) from exc
            chunks.extend(chunk)
            if len(chunks) > self.config.max_response_bytes:
                raise TTSMalformedStreamError("Gemini TTS response exceeds size limit")
        try:
            body = json.loads(chunks)
        except json.JSONDecodeError as exc:
            raise TTSMalformedStreamError("Gemini TTS returned malformed JSON") from exc
        if not isinstance(body, dict):
            raise TTSMalformedStreamError("Gemini TTS response must be an object")
        pcm = extract_pcm_from_gemini_response(body)
        if pcm is not None:
            yield pcm

    @staticmethod
    def _parse_body(data: str) -> dict[str, Any]:
        try:
            body = json.loads(data)
        except json.JSONDecodeError as exc:
            raise TTSMalformedStreamError("Gemini TTS returned malformed SSE JSON") from exc
        if not isinstance(body, dict):
            raise TTSMalformedStreamError("Gemini TTS SSE data must be an object")
        if "error" in body:
            error = body["error"]
            status = error.get("code") if isinstance(error, dict) else None
            if status in (401, 403):
                raise TTSProviderAuthError("Gemini TTS authentication failed", status)
            if status == 429:
                raise TTSProviderRateLimitError("Gemini TTS rate limit exceeded")
            raise TTSProviderUnavailableError("Gemini TTS stream failed", status)
        return body

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            raise TTSProviderAuthError("Gemini TTS authentication failed", status)
        if status == 429:
            retry_after: float | None = None
            try:
                retry_after = float(response.headers.get("retry-after", ""))
            except ValueError:
                pass
            raise TTSProviderRateLimitError("Gemini TTS rate limit exceeded", retry_after)
        if status >= 500:
            raise TTSProviderUnavailableError("Gemini TTS is unavailable", status)
        if status != 200:
            raise TTSProviderError("Gemini TTS rejected the request", status)
