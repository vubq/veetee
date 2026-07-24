from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Any

import structlog

from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import AudioChunk

logger = structlog.get_logger(__name__)


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
        self._connect_timeout = _bounded_int(
            self._config.get("connectTimeoutSeconds", 3), 1, 10
        )
        self._receive_timeout = _bounded_int(
            self._config.get("receiveTimeoutSeconds", 8), 1, 30
        )
        self._max_attempts = _bounded_int(self._config.get("maxAttempts", 2), 1, 3)
        self._local_prosody = self._config.get("localProsodyProcessing", True) is not False
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

        ffmpeg_arguments = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
        ]
        if self._local_prosody:
            ffmpeg_arguments.extend(
                [
                    "-filter:a",
                    f"atempo={self._rate:.3f},volume={self._volume:.3f}",
                ]
            )
        ffmpeg_arguments.extend(
            [
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                str(self._sample_rate),
                "pipe:1",
            ]
        )
        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_binary,
            *ffmpeg_arguments,
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
            for attempt in range(1, self._max_attempts + 1):
                context.checkpoint()
                remaining = context.remaining_seconds
                connect_timeout = min(
                    self._connect_timeout,
                    max(1, int(remaining / 4)),
                )
                receive_timeout = min(
                    self._receive_timeout,
                    max(1, int(remaining - connect_timeout - 0.25)),
                )
                communicate = edge_tts.Communicate(
                    text,
                    voice=self._voice,
                    rate=_edge_percent(1.0 if self._local_prosody else self._rate),
                    volume=_edge_percent(1.0 if self._local_prosody else self._volume),
                    pitch=_edge_pitch(self._pitch_hz),
                    connect_timeout=connect_timeout,
                    receive_timeout=receive_timeout,
                )
                attempt_received_audio = False
                stream = communicate.stream()
                try:
                    while True:
                        context.checkpoint()
                        try:
                            event = await asyncio.wait_for(
                                anext(stream),
                                timeout=min(
                                    float(receive_timeout),
                                    context.remaining_seconds,
                                ),
                            )
                        except StopAsyncIteration:
                            break
                        if event.get("type") != "audio":
                            continue
                        audio = event.get("data")
                        if not isinstance(audio, bytes) or not audio:
                            continue
                        attempt_received_audio = True
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
                    break
                except (
                    edge_tts.exceptions.EdgeTTSException,
                    TimeoutError,
                    OSError,
                ) as error:
                    if attempt_received_audio or attempt >= self._max_attempts:
                        raise
                    logger.warning(
                        "edge_tts_retry",
                        attempt=attempt,
                        max_attempts=self._max_attempts,
                        error=type(error).__name__,
                        voice=self._voice,
                    )
                    await asyncio.sleep(min(0.05, context.remaining_seconds))
                finally:
                    with suppress(Exception):
                        await stream.aclose()
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


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        number = int(value) if isinstance(value, int | float) else minimum
    except (TypeError, ValueError):
        return minimum
    return min(max(number, minimum), maximum)


def _edge_percent(value: float) -> str:
    return f"{round((value - 1.0) * 100):+d}%"


def _edge_pitch(value: float) -> str:
    return f"{round(value):+d}Hz"
