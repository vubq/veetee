"""Tests for the local VieNeu WAV-to-PCM adapter."""

import io
import wave

import httpx
import pytest

from veetee_server.pipeline.tts.vieneu import VieNeuTTSAdapter


def make_wav(frame_count: int = 1440, rate: int = 24000, channels: int = 1) -> bytes:
    raw = (b"\x01\x02" * frame_count * channels)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(raw)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_rate", [24000, 48000])
async def test_vieneu_converts_wav_to_contract_frames(source_rate: int) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=make_wav(1440 * 2 * source_rate // 24000, rate=source_rate),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = VieNeuTTSAdapter(client, "http://vieneu")
        frames = [chunk async for chunk in adapter.synthesize("Xin chào")]
    assert len(frames) == 2
    assert all(len(frame) == 2880 for frame in frames)


@pytest.mark.asyncio
async def test_vieneu_rejects_unsupported_sample_rate() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=make_wav(rate=8000))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = VieNeuTTSAdapter(client, "http://vieneu")
        with pytest.raises(Exception, match="sample rate is unsupported"):
            _ = [chunk async for chunk in adapter.synthesize("Xin chào")]
