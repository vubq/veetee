from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import sys
import unicodedata
from collections.abc import AsyncGenerator
from difflib import SequenceMatcher
from pathlib import Path

import structlog

from veetee_voice_server.conversation.cancellation import (
    OperationContext,
    await_operation,
)
from veetee_voice_server.conversation.types import AudioChunk
from veetee_voice_server.tools.media import MediaCandidate, MediaSearchResult

logger = structlog.get_logger(__name__)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ERROR_BYTES = 16 * 1024


class YouTubeMusicProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class YouTubeMusicProvider:
    """Bounded yt-dlp search and FFmpeg PCM stream adapter.

    The model supplies structured metadata only. This adapter constructs and
    validates the provider item URL internally; it never accepts a URL, shell
    fragment or downloader option from a tool call.
    """

    def __init__(
        self,
        *,
        output_sample_rate: int = 24_000,
        search_results: int = 8,
        search_seconds: float = 15.0,
        pcm_chunk_ms: int = 60,
        ffmpeg_binary: str = "ffmpeg",
        process_shutdown_seconds: float = 3.0,
        cookie_file: Path | None = None,
    ) -> None:
        if not 8_000 <= output_sample_rate <= 48_000:
            raise ValueError("YouTube Music output sample rate is invalid")
        if not 1 <= search_results <= 12:
            raise ValueError("YouTube Music search result bound is invalid")
        if not 1.0 <= search_seconds <= 30.0:
            raise ValueError("YouTube Music search deadline is invalid")
        if not 20 <= pcm_chunk_ms <= 200:
            raise ValueError("YouTube Music PCM chunk duration is invalid")
        if not 0.5 <= process_shutdown_seconds <= 10.0:
            raise ValueError("YouTube Music process shutdown deadline is invalid")
        if importlib.util.find_spec("yt_dlp") is None:
            raise YouTubeMusicProviderError("youtube_music_downloader_unavailable")
        resolved_ffmpeg = shutil.which(ffmpeg_binary)
        if resolved_ffmpeg is None:
            raise YouTubeMusicProviderError("youtube_music_ffmpeg_unavailable")
        downloader_options: list[str] = []
        node = shutil.which("node")
        if node is not None:
            downloader_options.extend(("--js-runtimes", f"node:{node}"))
        if cookie_file is not None:
            resolved_cookie = cookie_file.expanduser().resolve()
            if not resolved_cookie.is_file():
                raise YouTubeMusicProviderError("youtube_music_cookie_file_unavailable")
            if resolved_cookie.stat().st_mode & 0o077:
                raise YouTubeMusicProviderError("youtube_music_cookie_file_permissions")
            downloader_options.extend(("--cookies", str(resolved_cookie)))

        self._output_sample_rate = output_sample_rate
        self._search_results = search_results
        self._search_seconds = search_seconds
        self._pcm_chunk_bytes = output_sample_rate * pcm_chunk_ms // 1_000 * 2
        self._ffmpeg_binary = resolved_ffmpeg
        self._process_shutdown_seconds = process_shutdown_seconds
        self._downloader_options = tuple(downloader_options)
        self._authenticated = cookie_file is not None

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    async def search(
        self,
        *,
        mode: str,
        title: str | None,
        artist: str | None,
        query: str | None,
        context: OperationContext,
    ) -> MediaSearchResult:
        search_query = self._search_query(mode, title, artist, query)
        search_context = context.child(self._search_seconds)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "yt_dlp",
            *self._downloader_options,
            "--ignore-errors",
            "--no-warnings",
            "--flat-playlist",
            "--dump-single-json",
            "--skip-download",
            "--playlist-end",
            str(self._search_results),
            f"ytsearch{self._search_results}:{search_query}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await await_operation(process.communicate(), search_context)
        finally:
            current_task = asyncio.current_task()
            if context.token.cancelled or (
                current_task is not None and current_task.cancelling() > 0
            ):
                self._kill_process(process)
            await self._stop_process(process)
        context.checkpoint()
        if len(stdout) > _MAX_METADATA_BYTES or len(stderr) > _MAX_ERROR_BYTES:
            raise YouTubeMusicProviderError("youtube_music_search_output_too_large")
        if process.returncode not in {0, None}:
            raise YouTubeMusicProviderError(self._provider_error_code(stderr, "search"))
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise YouTubeMusicProviderError("youtube_music_search_invalid") from error
        candidates = self._candidates(payload)
        if not candidates:
            return MediaSearchResult()
        if mode == "any_track":
            return MediaSearchResult(selected=candidates[0])
        assert title is not None and artist is not None
        return self._select_specific(candidates, title=title, artist=artist)

    async def stream(
        self, candidate: MediaCandidate, context: OperationContext
    ) -> AsyncGenerator[AudioChunk, None]:
        if not _VIDEO_ID.fullmatch(candidate.provider_item_id):
            raise YouTubeMusicProviderError("youtube_music_item_id_invalid")
        source_url = (
            "https://www.youtube.com/watch?v=" + candidate.provider_item_id
        )
        downloader = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "yt_dlp",
            *self._downloader_options,
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--downloader",
            "ffmpeg",
            "-f",
            "worst[protocol^=m3u8][acodec!=none]/bestaudio/best",
            "-o",
            "-",
            "--",
            source_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert downloader.stdout is not None
        decoder = await asyncio.create_subprocess_exec(
            self._ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            str(self._output_sample_rate),
            "-f",
            "s16le",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert decoder.stdin is not None
        assert decoder.stdout is not None
        assert downloader.stderr is not None
        assert decoder.stderr is not None
        feeder = asyncio.create_task(
            self._feed_decoder(downloader.stdout, decoder.stdin),
            name=f"youtube-music-feed:{candidate.provider_item_id}",
        )
        downloader_error = asyncio.create_task(
            self._read_bounded(downloader.stderr, _MAX_ERROR_BYTES),
            name=f"youtube-music-downloader-stderr:{candidate.provider_item_id}",
        )
        decoder_error = asyncio.create_task(
            self._read_bounded(decoder.stderr, _MAX_ERROR_BYTES),
            name=f"youtube-music-decoder-stderr:{candidate.provider_item_id}",
        )
        sequence = 0
        emitted = False
        buffer = bytearray()
        try:
            while True:
                chunk = await await_operation(
                    decoder.stdout.read(self._pcm_chunk_bytes), context
                )
                if not chunk:
                    break
                buffer.extend(chunk)
                while len(buffer) >= self._pcm_chunk_bytes:
                    context.checkpoint()
                    data = bytes(buffer[: self._pcm_chunk_bytes])
                    del buffer[: self._pcm_chunk_bytes]
                    emitted = True
                    yield AudioChunk(
                        sequence=sequence,
                        sample_rate=self._output_sample_rate,
                        encoding="pcm_s16le",
                        data=data,
                    )
                    sequence += 1
            if buffer:
                if len(buffer) % 2:
                    buffer.pop()
                if buffer:
                    emitted = True
                    yield AudioChunk(
                        sequence=sequence,
                        sample_rate=self._output_sample_rate,
                        encoding="pcm_s16le",
                        data=bytes(buffer),
                        final=True,
                    )
            await await_operation(feeder, context)
            await self._wait_process(decoder, context)
            await self._wait_process(downloader, context)
            downloader_detail = await self._settle_task(downloader_error)
            if decoder.returncode != 0 or downloader.returncode != 0:
                raise YouTubeMusicProviderError(
                    self._provider_error_code(downloader_detail, "stream")
                )
            if not emitted:
                raise YouTubeMusicProviderError("youtube_music_stream_empty")
        finally:
            current_task = asyncio.current_task()
            force = context.token.cancelled or (
                current_task is not None and current_task.cancelling() > 0
            )
            errors = await self._cleanup_stream_processes(
                downloader,
                decoder,
                feeder,
                downloader_error,
                decoder_error,
                force=force,
            )
            if not emitted and not context.token.cancelled:
                logger.warning(
                    "youtube_music_stream_no_audio",
                    downloader_error=self._error_code(errors[0]),
                    decoder_error=self._error_code(errors[1]),
                )

    async def _cleanup_stream_processes(
        self,
        downloader: asyncio.subprocess.Process,
        decoder: asyncio.subprocess.Process,
        feeder: asyncio.Task[None],
        downloader_error: asyncio.Task[bytes],
        decoder_error: asyncio.Task[bytes],
        *,
        force: bool,
    ) -> list[object]:
        # Process.wait() can deadlock with PIPE output that is no longer consumed.
        # On cancellation, signal both children before the first await, then stop
        # the feeder and drain both stdout pipes concurrently with process reaping.
        if force:
            self._kill_process(decoder)
            self._kill_process(downloader)
            feeder.cancel()
        assert decoder.stdin is not None
        assert decoder.stdout is not None
        assert downloader.stdout is not None
        if not decoder.stdin.is_closing():
            decoder.stdin.close()
        await self._settle_task(feeder)
        stdout_tasks = [
            asyncio.create_task(downloader.stdout.read()),
            asyncio.create_task(decoder.stdout.read()),
        ]
        stop_tasks = [
            asyncio.create_task(self._stop_process(decoder)),
            asyncio.create_task(self._stop_process(downloader)),
        ]
        errors = await asyncio.gather(
            self._settle_task(downloader_error),
            self._settle_task(decoder_error),
        )
        await asyncio.gather(*stdout_tasks, *stop_tasks, return_exceptions=True)
        return list(errors)

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.kill()
        except ProcessLookupError:
            pass

    async def _settle_task(self, task: asyncio.Task[object]) -> object:
        try:
            return await asyncio.wait_for(
                task, timeout=self._process_shutdown_seconds
            )
        except TimeoutError as error:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return error
        except BaseException as error:
            return error

    async def _wait_process(
        self, process: asyncio.subprocess.Process, context: OperationContext
    ) -> None:
        if process.returncode is not None:
            return
        await await_operation(
            process.wait(), context.child(self._process_shutdown_seconds)
        )

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self._process_shutdown_seconds
            )
        except TimeoutError:
            self._kill_process(process)
            await process.wait()

    @staticmethod
    async def _feed_decoder(
        source: asyncio.StreamReader, sink: asyncio.StreamWriter
    ) -> None:
        try:
            while data := await source.read(64 * 1024):
                sink.write(data)
                await sink.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if not sink.is_closing():
                sink.close()
                try:
                    await sink.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    @staticmethod
    async def _read_bounded(reader: asyncio.StreamReader, maximum: int) -> bytes:
        result = bytearray()
        while chunk := await reader.read(min(4096, maximum + 1 - len(result))):
            result.extend(chunk)
            if len(result) > maximum:
                break
        return bytes(result)

    @staticmethod
    def _error_code(value: object) -> str:
        if isinstance(value, bytes) and value:
            return "provider_error"
        if isinstance(value, BaseException):
            return type(value).__name__
        return "none"

    @staticmethod
    def _provider_error_code(value: object, operation: str) -> str:
        detail = value.lower() if isinstance(value, bytes) else b""
        if b"http error 429" in detail or b"not a bot" in detail:
            return "youtube_music_rate_limited"
        if b"http error 403" in detail or b"forbidden" in detail:
            return "youtube_music_stream_forbidden"
        if b"sign in" in detail or b"cookies" in detail:
            return "youtube_music_auth_required"
        return f"youtube_music_{operation}_failed"

    @staticmethod
    def _search_query(
        mode: str,
        title: str | None,
        artist: str | None,
        query: str | None,
    ) -> str:
        if mode == "specific_track" and title and artist:
            return f"{title.strip()} {artist.strip()}"[:480]
        if mode == "any_track" and query:
            return query.strip()[:240]
        raise ValueError("YouTube Music search metadata is incomplete")

    @classmethod
    def _candidates(cls, payload: object) -> tuple[MediaCandidate, ...]:
        if not isinstance(payload, dict):
            return ()
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return ()
        candidates: list[MediaCandidate] = []
        seen: set[str] = set()
        for value in entries:
            if not isinstance(value, dict):
                continue
            item_id = value.get("id")
            if not isinstance(item_id, str) or not _VIDEO_ID.fullmatch(item_id):
                continue
            if item_id in seen:
                continue
            title = cls._bounded_text(value.get("track") or value.get("title"), 240)
            artist = cls._bounded_text(
                value.get("artist") or value.get("channel") or value.get("uploader"),
                240,
            )
            if not title or not artist:
                continue
            album = cls._bounded_text(value.get("album"), 240) or None
            duration_value = value.get("duration")
            duration = (
                max(0, min(int(duration_value), 24 * 60 * 60))
                if isinstance(duration_value, int | float)
                and not isinstance(duration_value, bool)
                else None
            )
            seen.add(item_id)
            candidates.append(
                MediaCandidate(item_id, title, artist, album, duration)
            )
        return tuple(candidates)

    @classmethod
    def _select_specific(
        cls,
        candidates: tuple[MediaCandidate, ...],
        *,
        title: str,
        artist: str,
    ) -> MediaSearchResult:
        ranked = sorted(
            candidates,
            key=lambda item: (
                cls._match_score(title, item.title)
                + cls._match_score(artist, item.artist)
            ),
            reverse=True,
        )
        best = ranked[0]
        best_score = (
            cls._match_score(title, best.title)
            + cls._match_score(artist, best.artist)
        ) / 2
        next_score = (
            (
                cls._match_score(title, ranked[1].title)
                + cls._match_score(artist, ranked[1].artist)
            )
            / 2
            if len(ranked) > 1
            else 0.0
        )
        if best_score >= 0.82 and best_score - next_score >= 0.015:
            return MediaSearchResult(selected=best)
        return MediaSearchResult(alternatives=tuple(ranked[:8]))

    @staticmethod
    def _bounded_text(value: object, maximum: int) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.split())[:maximum]

    @staticmethod
    def _match_score(expected: str, actual: str) -> float:
        left = YouTubeMusicProvider._normalized(expected)
        right = YouTubeMusicProvider._normalized(actual)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        if left in right:
            return 0.96
        return SequenceMatcher(None, left, right).ratio()

    @staticmethod
    def _normalized(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value).casefold()
        return " ".join(
            "".join(
                character if character.isalnum() else ""
                if unicodedata.combining(character)
                else " "
                for character in decomposed
            )
            .split()
        )


__all__ = ["YouTubeMusicProvider", "YouTubeMusicProviderError"]
