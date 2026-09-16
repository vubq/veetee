"""Background music playback streamed to the client as paced Opus audio.

Uses the stock ``tts:start / sentence_start / binary / tts:stop`` wire
messages, so no firmware change is needed: the track title shows on the
device display like any spoken sentence. Playback runs outside AI turns in
its own task; abort/listen:start/session-close stop it via :meth:`stop`.

Only one track plays at a time. History enables next/previous navigation.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

import numpy as np

from core.audio_utils import AudioCodec
from core.protocol import make_tts_message, pack_audio_payload

logger = logging.getLogger("MusicPlayer")

SAMPLE_RATE = 24000
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono


@dataclass
class MusicTrack:
    video_id: str = ""
    title: str = ""
    channel: str = ""
    duration_s: float = 0.0
    stream_url: str = ""


async def ffmpeg_pcm24_frames(
    track: MusicTrack,
    *,
    timeout_s: float = 20.0,
) -> AsyncIterator[bytes]:
    """Yield raw s16le/24kHz/mono PCM chunks decoded from a track stream URL."""
    stream_url = track.stream_url
    if not stream_url:
        raise ValueError("track stream_url is empty")
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg, "-nostdin", "-loglevel", "error",
        "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", stream_url,
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-vn", "-sn", "-dn", "pipe:1",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot start ffmpeg: {exc}") from exc

    chunks_yielded = 0
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(FRAME_BYTES * 8), timeout=timeout_s)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                logger.warning("ffmpeg stream read timeout/incomplete: %s", exc)
                break
            if not chunk:
                break
            chunks_yielded += 1
            yield chunk
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            if proc.returncode is not None and proc.returncode not in (0, -15):
                err_msg = (stderr or b"").decode("utf-8", "ignore").strip()
                logger.warning("ffmpeg exited with code %s: %s", proc.returncode, err_msg)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                logger.warning("Timed out while reaping ffmpeg process")

    if chunks_yielded == 0:
        raise RuntimeError("ffmpeg produced no audio chunks (stream failed or unreachable)")


class MusicPlayer:
    """Owns music playback state for one client session."""

    def __init__(
        self,
        *,
        send_text: Callable[[str], Awaitable[bool]],
        send_binary: Callable[[bytes], Awaitable[bool]],
        session_id: str,
        version: Any = 1,
        stall_timeout_s: float = 12.0,
        frame_source_factory: Optional[
            Callable[..., AsyncIterator[bytes]]
        ] = None,
    ):
        self._send_text = send_text
        self._send_binary = send_binary
        self._session_id = session_id
        # Version may be renegotiated per hello; accept a value or getter.
        self._version = version
        self._stall_timeout_s = stall_timeout_s
        self._frame_source_factory = frame_source_factory or ffmpeg_pcm24_frames
        self._codec = AudioCodec(
            in_sample_rate=16000, out_sample_rate=SAMPLE_RATE,
            frame_duration_ms=FRAME_MS)
        self._state = "idle"
        self._envelope_active = False
        self._current: Optional[MusicTrack] = None
        self._pending_track: Optional[MusicTrack] = None
        self._history: List[MusicTrack] = []
        self._history_index: int = -1
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._frames_sent = 0

    def _protocol_version(self) -> int:
        version = self._version() if callable(self._version) else self._version
        try:
            return int(getattr(version, "value", version) or 1)
        except (TypeError, ValueError):
            return 1

    @property
    def state(self) -> str:
        return self._state

    @property
    def playing(self) -> bool:
        return self._state in ("playing", "paused") and self._task is not None \
            and not self._task.done()

    def set_pending(self, track: MusicTrack) -> Dict[str, Any]:
        """Queue a track to start cleanly after the AI confirmation finishes."""
        self._history = self._history[:self._history_index + 1]
        self._history.append(track)
        self._history_index = len(self._history) - 1
        self._pending_track = track
        return {
            "status": "ready",
            "title": track.title,
            "channel": track.channel,
            "video_id": track.video_id,
            "duration_s": track.duration_s,
        }

    def clear_pending(self) -> None:
        self._pending_track = None

    async def start_pending(self) -> None:
        """Start the queued track after AI voice speech has finished."""
        if self._pending_track is not None:
            track = self._pending_track
            self._pending_track = None
            await self._start(track)

    async def play(self, track: MusicTrack) -> Dict[str, Any]:
        """Stop anything current and start a track from the beginning immediately."""
        self.clear_pending()
        # Truncate forward history on a fresh play, then append.
        self._history = self._history[:self._history_index + 1]
        self._history.append(track)
        self._history_index = len(self._history) - 1
        await self._start(track)
        return {
            "status": "playing",
            "title": track.title,
            "channel": track.channel,
            "video_id": track.video_id,
            "duration_s": track.duration_s,
        }

    async def _start(self, track: MusicTrack) -> None:
        # Replacing a live track must close the stock firmware TTS envelope
        # before the next track opens a new one.  Callers that intentionally
        # take over the protocol lifecycle (listen:start / wake / session
        # close) use stop(announce=False) themselves; an internal track
        # replacement has no such outer stop message.
        await self.stop(announce=True)
        self._stop_event.clear()
        self._pause_event.set()
        self._envelope_active = False
        self._current = track
        self._frames_sent = 0
        self._state = "playing"
        self._task = asyncio.create_task(self._run(track))
        logger.info("Music play session=%s title=%r", self._session_id, track.title)

    async def _frame_stream(self, track: MusicTrack) -> AsyncIterator[bytes]:
        try:
            source = self._frame_source_factory(track, timeout_s=self._stall_timeout_s)
        except TypeError:
            source = self._frame_source_factory(track)
        async for chunk in source:
            yield chunk

    async def _run(self, track: MusicTrack) -> None:
        try:
            if not await self._send_text(
                    make_tts_message(self._session_id, "start")):
                return
            # Treat the envelope as open as soon as tts:start is accepted.
            # This lets stop() close it even if cancellation lands before the
            # following sentence_start send completes.
            self._envelope_active = True
            if not await self._send_text(make_tts_message(
                    self._session_id, "sentence_start", f"♫ {track.title}")):
                return
            remainder = bytearray()
            loop = asyncio.get_running_loop()
            period = FRAME_MS / 1000.0
            next_due = loop.time()
            async for chunk in self._frame_stream(track):
                if self._stop_event.is_set():
                    return
                while not self._pause_event.is_set():
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(0.05)
                if not self._envelope_active:
                    if not await self._send_text(
                            make_tts_message(self._session_id, "start")):
                        return
                    self._envelope_active = True
                    if not await self._send_text(make_tts_message(
                            self._session_id, "sentence_start", f"♫ {track.title}")):
                        return
                    next_due = loop.time()
                frames = self._codec.chunk_pcm_to_opus_frames(
                    np.frombuffer(chunk, dtype="<i2"), remainder)
                for opus in frames:
                    if self._stop_event.is_set():
                        return
                    while not self._pause_event.is_set():
                        if self._stop_event.is_set():
                            return
                        await asyncio.sleep(0.05)
                    if not self._envelope_active:
                        if not await self._send_text(
                                make_tts_message(self._session_id, "start")):
                            return
                        self._envelope_active = True
                        if not await self._send_text(make_tts_message(
                                self._session_id, "sentence_start", f"♫ {track.title}")):
                            return
                        next_due = loop.time()
                    packet = pack_audio_payload(opus, self._protocol_version())
                    if not await self._send_binary(packet):
                        logger.warning("Music send failed session=%s",
                                       self._session_id)
                        return
                    self._frames_sent += 1
                    next_due += period
                    delay = next_due - loop.time()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    else:
                        next_due = loop.time()
            for opus in self._codec.flush_remainder_to_opus_frame(remainder):
                if self._stop_event.is_set():
                    return
                if not await self._send_binary(
                        pack_audio_payload(opus, self._protocol_version())):
                    return
                self._frames_sent += 1
            if self._envelope_active:
                await self._send_text(make_tts_message(self._session_id, "stop"))
                self._envelope_active = False
            logger.info("Music track ended session=%s title=%r frames=%d",
                        self._session_id, track.title, self._frames_sent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._state = "failed"
            logger.warning("Music playback failed session=%s error=%s",
                           self._session_id, exc)
            if self._envelope_active:
                try:
                    await self._send_text(make_tts_message(self._session_id, "stop"))
                except Exception:
                    pass
                self._envelope_active = False
        finally:
            if self._state != "failed":
                self._state = "idle"
            self._task = None

    async def stop(self, *, announce: bool = True) -> Dict[str, Any]:
        """Stop playback. Sends tts:stop unless the caller already does."""
        self.clear_pending()
        was_playing = self._state in ("playing", "paused")
        task, self._task = self._task, None
        self._stop_event.set()
        self._pause_event.set()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._state = "idle"
        if was_playing and announce and self._envelope_active:
            try:
                await self._send_text(make_tts_message(self._session_id, "stop"))
            except Exception as exc:
                logger.debug("Music stop announce failed: %s", exc)
        self._envelope_active = False
        return {"status": "stopped" if was_playing else "idle"}

    async def pause(self) -> Dict[str, Any]:
        if self._state != "playing":
            return {"status": self._state}
        self._pause_event.clear()
        self._state = "paused"
        # Stock Xiaozhi uses tts:stop to leave Speaking. Closing the envelope
        # here is required before normal assistant TTS can take over. _run()
        # opens a fresh start/sentence_start envelope before the first frame
        # after resume.
        if self._envelope_active:
            try:
                await self._send_text(make_tts_message(self._session_id, "stop"))
            except Exception as exc:
                logger.debug("Music pause stop announce failed: %s", exc)
        self._envelope_active = False
        return {"status": "paused", "title": self._current.title if self._current else ""}

    async def resume(self) -> Dict[str, Any]:
        if self._state != "paused":
            return {"status": self._state}
        self._state = "playing"
        self._pause_event.set()
        return {"status": "playing", "title": self._current.title if self._current else ""}

    async def play_index(self, index: int) -> Dict[str, Any]:
        """Play a track from history by index (next/previous navigation)."""
        if not self._history:
            return {"status": "error", "error": "no history", "message": "Chưa có danh sách bài hát."}
        if index < 0 or index >= len(self._history):
            return {
                "status": "error",
                "error": "out_of_bounds",
                "message": "Không có bài hát phù hợp ở vị trí này.",
            }
        self._history_index = index
        await self._start(self._history[index])
        return {"status": "playing", "title": self._history[index].title}

    async def next_track(self) -> Dict[str, Any]:
        if not self._history or self._history_index + 1 >= len(self._history):
            return {
                "status": "error",
                "error": "no_next_track",
                "message": "Không có bài tiếp theo trong danh sách.",
            }
        return await self.play_index(self._history_index + 1)

    async def previous_track(self) -> Dict[str, Any]:
        if not self._history or self._history_index - 1 < 0:
            return {
                "status": "error",
                "error": "no_previous_track",
                "message": "Không có bài trước đó trong danh sách.",
            }
        return await self.play_index(self._history_index - 1)

    def status(self) -> Dict[str, Any]:
        current = self._current
        return {
            "state": self._state,
            "title": current.title if current else "",
            "channel": current.channel if current else "",
            "position_s": round(self._frames_sent * FRAME_MS / 1000.0, 1),
            "history": len(self._history),
            "frames_sent": self._frames_sent,
        }

    async def close(self) -> None:
        await self.stop(announce=False)
