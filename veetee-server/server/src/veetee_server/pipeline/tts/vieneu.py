"""Low-latency local VieNeu-TTS adapter."""

from __future__ import annotations

import asyncio
import io
import logging
import struct
import wave
from collections.abc import AsyncIterator

import httpx

from veetee_server.audio.codec import DOWNLINK_PCM_FORMAT

from .errors import TTSFormatError, TTSProviderUnavailableError

logger = logging.getLogger("veetee.tts.vieneu")


def _resample_mono_s16le(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Resample bounded mono PCM without adding an audio dependency."""
    if source_rate == target_rate:
        return pcm
    source = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    target_count = round(len(source) * target_rate / source_rate)
    output: list[int] = []
    for index in range(target_count):
        position = index * source_rate / target_rate
        left = min(int(position), len(source) - 1)
        right = min(left + 1, len(source) - 1)
        fraction = position - left
        sample = round(source[left] + (source[right] - source[left]) * fraction)
        output.append(max(-32768, min(32767, sample)))
    return struct.pack(f"<{len(output)}h", *output)


class VieNeuTTSRuntime:
    """Resident HTTP client for a local VieNeu-TTS process."""

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def is_ready(self) -> bool:
        return self._client is not None

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def create_adapter(self) -> VieNeuTTSAdapter:
        if self._client is None:
            raise RuntimeError("VieNeu TTS runtime is not ready")
        return VieNeuTTSAdapter(self._client, self.base_url, self.timeout_seconds)


class VieNeuTTSAdapter:
    """Converts VieNeu's WAV response to 60 ms PCM chunks."""

    def __init__(
        self, client: httpx.AsyncClient, base_url: str, timeout_seconds: float = 8.0
    ) -> None:
        self._client = client
        self._url = f"{base_url}/tts"
        self._timeout_seconds = timeout_seconds

    async def synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        if not sentence.strip():
            return
        try:
            response = await asyncio.wait_for(
                self._client.post(self._url, json={"text": sentence}),
                self._timeout_seconds,
            )
            response.raise_for_status()
        except (TimeoutError, httpx.HTTPError) as exc:
            logger.warning(
                "vieneu_request_failed",
                extra={"context": {"reason": type(exc).__name__}},
            )
            raise TTSProviderUnavailableError("VieNeu TTS request failed") from exc

        try:
            with wave.open(io.BytesIO(response.content), "rb") as wav:
                if wav.getnchannels() != DOWNLINK_PCM_FORMAT.channels or wav.getsampwidth() != 2:
                    raise TTSFormatError("VieNeu audio must be mono s16le PCM")
                source_rate = wav.getframerate()
                pcm = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError) as exc:
            raise TTSFormatError("VieNeu returned malformed WAV") from exc
        if source_rate not in {16000, 22050, 24000, 44100, 48000}:
            raise TTSFormatError(f"VieNeu sample rate is unsupported: {source_rate} Hz")
        pcm = _resample_mono_s16le(pcm, source_rate, DOWNLINK_PCM_FORMAT.sample_rate)

        frame_bytes = DOWNLINK_PCM_FORMAT.expected_bytes
        for offset in range(0, len(pcm), frame_bytes):
            frame = pcm[offset : offset + frame_bytes]
            yield frame.ljust(frame_bytes, b"\x00")
