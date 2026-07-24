from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import AudioChunk


class EdgeTtsProvider:
    """Microsoft Edge online TTS adapter with PCM output for the device wire contract."""

    def __init__(
        self,
        *,
        voice: str = "vi-VN-HoaiMyNeural",
        rate: float = 1.0,
        pitch_hz: float = 0.0,
        volume: float = 1.0,
        output_sample_rate: int = 24_000,
        config: Mapping[str, Any] | None = None,
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        self._config = dict(config or {})
        self._voice = str(self._config.get("voice", voice))
        self._rate = _bounded_float(self._config.get("rate", rate), 0.5, 2.0)
        self._pitch_hz = _bounded_float(self._config.get("pitchHz", pitch_hz), -100.0, 100.0)
        self._volume = _bounded_float(self._config.get("volume", volume), 0.0, 1.5)
        self._sample_rate = max(
            8_000, min(int(self._config.get("outputSampleRate", output_sample_rate)), 48_000)
        )
        self._ffmpeg_binary = ffmpeg_binary

    async def prewarm(self) -> None:
        try:
            import edge_tts
        except ImportError as error:
            raise RuntimeError("edge-tts is not installed") from error
        # Import-only prewarm avoids an external network request during startup.
        del edge_tts

    async def synthesize(
        self, text: str, locale: str, context: OperationContext
    ) -> AsyncIterator[AudioChunk]:
        del locale
        if not text.strip():
            return
        context.checkpoint()
        try:
            import edge_tts
        except ImportError as error:
            raise RuntimeError("edge-tts is not installed") from error

        communicate = edge_tts.Communicate(
            text,
            voice=self._voice,
            rate=_edge_percent(self._rate),
            volume=_edge_percent(self._volume),
            pitch=_edge_pitch(self._pitch_hz),
        )
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(self._sample_rate),
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        stdout = process.stdout
        stderr = process.stderr
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def read_pcm() -> None:
            try:
                while True:
                    chunk = await stdout.read(8_192)
                    if not chunk:
                        break
                    await queue.put(chunk)
            finally:
                await queue.put(None)

        reader = asyncio.create_task(read_pcm())
        sequence = 0
        reader_done_seen = False
        pending_pcm = bytearray()

        def aligned_pcm(chunk: bytes) -> bytes:
            pending_pcm.extend(chunk)
            aligned_length = len(pending_pcm) - (len(pending_pcm) % 2)
            if aligned_length == 0:
                return b""
            output = bytes(pending_pcm[:aligned_length])
            del pending_pcm[:aligned_length]
            return output

        try:
            async for event in communicate.stream():
                context.checkpoint()
                if event.get("type") != "audio":
                    continue
                audio = event.get("data")
                if not isinstance(audio, bytes) or not audio:
                    continue
                process.stdin.write(audio)
                await process.stdin.drain()
                while not queue.empty():
                    pcm = queue.get_nowait()
                    if pcm is None:
                        reader_done_seen = True
                        break
                    pcm = aligned_pcm(pcm)
                    if pcm:
                        yield AudioChunk(
                            sequence=sequence,
                            sample_rate=self._sample_rate,
                            encoding="pcm_s16le",
                            data=pcm,
                        )
                        sequence += 1
            process.stdin.close()
            await process.wait()
            if not reader_done_seen:
                while True:
                    pcm = await queue.get()
                    if pcm is None:
                        break
                    pcm = aligned_pcm(pcm)
                    if pcm:
                        yield AudioChunk(
                            sequence=sequence,
                            sample_rate=self._sample_rate,
                            encoding="pcm_s16le",
                            data=pcm,
                        )
                        sequence += 1
            if process.returncode:
                detail = (await stderr.read()).decode(errors="replace")[:240] if stderr else ""
                raise RuntimeError(f"ffmpeg could not decode Edge TTS audio: {detail}")
            if pending_pcm:
                raise RuntimeError("ffmpeg returned an incomplete PCM16 sample")
            yield AudioChunk(
                sequence=sequence,
                sample_rate=self._sample_rate,
                encoding="pcm_s16le",
                data=b"",
                final=True,
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        finally:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def _bounded_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value) if isinstance(value, (int, float)) else minimum
    except (TypeError, ValueError):
        return minimum
    return min(max(number, minimum), maximum)


def _edge_percent(value: float) -> str:
    return f"{round((value - 1.0) * 100):+d}%"


def _edge_pitch(value: float) -> str:
    return f"{round(value):+d}Hz"
