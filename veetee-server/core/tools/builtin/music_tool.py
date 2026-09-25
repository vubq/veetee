"""YouTube music search/play/control tools (native, server-side).

Flow is AI-decided: ``music_search`` lists candidates, the model picks an
exact ``video_id`` or asks the user when ambiguous, then ``music_play``
starts board playback. ``music_control`` stops/pauses/resumes/navigates.
No keyword routing anywhere: the descriptions below are the whole contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
from typing import Any, Dict, List, Optional

from core.music_player import MusicPlayer, MusicTrack
from core.tools.base import ToolDescriptor

logger = logging.getLogger("MusicTools")


class ResolvedStreamURL(str):
    """String-compatible resolved URL carrying yt-dlp HTTP request headers."""

    def __new__(cls, value: str, http_headers: Optional[Dict[str, str]] = None):
        obj = str.__new__(cls, value)
        obj.http_headers = dict(http_headers or {})
        return obj


def _ytdlp_base() -> List[str]:
    direct = shutil.which("yt-dlp")
    if direct:
        return [direct]
    return [sys.executable, "-m", "yt_dlp"]


async def _kill_and_reap(proc) -> None:
    """Kill a child process and reap it so timeout/cancel cannot leave zombies."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.communicate(), timeout=3.0)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            logger.warning("Timed out while reaping yt-dlp process")


async def ytdlp_search(
    query: str, *, max_results: int = 5, timeout_s: float = 20.0
) -> List[Dict[str, Any]]:
    """Search YouTube, return [{video_id, title, channel, duration_s}]."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    cmd = _ytdlp_base() + [
        f"ytsearch{max(1, min(int(max_results), 10))}:{query}",
        "--dump-single-json", "--flat-playlist", "--no-warnings",
        "--no-playlist", "--socket-timeout", "10",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"music search unavailable: {exc}") from exc
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        await _kill_and_reap(proc)
        raise TimeoutError("music search timed out") from exc
    except asyncio.CancelledError:
        await _kill_and_reap(proc)
        raise
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"music search returned bad data: {exc}") from exc
    candidates = []
    for entry in (data.get("entries") or [])[:max_results]:
        video_id = str(entry.get("id") or "").strip()
        if not video_id:
            continue
        candidates.append({
            "video_id": video_id,
            "title": str(entry.get("title") or "")[:120],
            "channel": str(entry.get("uploader") or entry.get("channel") or "")[:80],
            "duration_s": entry.get("duration") or 0,
        })
    return candidates


async def ytdlp_resolve_url(video_id: str, *, timeout_s: float = 20.0) -> str:
    """Resolve a YouTube video id to a direct audio URL plus request headers.

    The return value remains string-compatible for callers/tests, while the
    ``http_headers`` attribute carries the exact headers yt-dlp used to obtain
    the signed GoogleVideo URL. Passing only the URL to ffmpeg can produce an
    immediate HTTP 403 when its default User-Agent differs from yt-dlp's.
    """
    video_id = (video_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("video_id must be an 11-character YouTube ID")
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = _ytdlp_base() + [
        "-J", "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "--ignore-config", "--no-playlist", "--no-warnings",
        "--socket-timeout", "10", "--", watch_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"music resolve unavailable: {exc}") from exc
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        await _kill_and_reap(proc)
        raise TimeoutError("music resolve timed out") from exc
    except asyncio.CancelledError:
        await _kill_and_reap(proc)
        raise
    try:
        data = json.loads(raw.decode("utf-8", "ignore"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"music resolve returned bad data: {exc}") from exc

    requested = data.get("requested_downloads") or []
    selected = requested[0] if requested and isinstance(requested[0], dict) else data
    url = str(selected.get("url") or data.get("url") or "").strip()
    if not url.startswith("http"):
        raise RuntimeError("music resolve produced no stream URL")

    raw_headers = selected.get("http_headers") or data.get("http_headers") or {}
    headers: Dict[str, str] = {}
    if isinstance(raw_headers, dict):
        for key, value in raw_headers.items():
            name = str(key or "").strip()
            text = str(value or "").strip()
            if not name or not text or "\r" in name or "\n" in name or "\r" in text or "\n" in text:
                continue
            headers[name] = text
    return ResolvedStreamURL(url, headers)


class MusicToolProvider:
    """Binds music tools to one session's player. Handlers are methods."""

    def __init__(
        self,
        player: MusicPlayer,
        *,
        search_results: int = 5,
        resolve_timeout_s: float = 20.0,
    ):
        self._player = player
        self._search_results = max(1, min(int(search_results), 10))
        self._resolve_timeout_s = max(5.0, float(resolve_timeout_s))
        self._last_candidates: Dict[str, Dict[str, Any]] = {}

    def descriptors(self) -> List[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="music_play",
                description=(
                    "Phát/đổi bài nhạc thật. query là tên, ca sĩ hoặc cụm user muốn nghe; "
                    "follow-up một cụm ngắn đã là query hợp lệ và không cần hỏi lại nếu đủ dữ kiện. "
                    "Nếu đã chọn từ music_search có thể thêm video_id. Đổi bài không cần xác nhận riêng."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                            "description": "Tên bài, ca sĩ hoặc cụm tìm kiếm user yêu cầu.",
                        },
                        "video_id": {
                            "type": "string",
                            "minLength": 11,
                            "maxLength": 11,
                            "pattern": "^[A-Za-z0-9_-]{11}$",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.play_music,
                timeout_ms=30000,
                read_only=False,
                idempotent=False,
                concurrency_group="music",
            ),
            ToolDescriptor(
                name="music_search",
                description=(
                    "Tìm ứng viên YouTube khi cần so sánh/chọn. Lệnh phát trực tiếp dùng music_play."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.search_music,
                timeout_ms=25000,
                read_only=True,
                idempotent=True,
                concurrency_group="music",
            ),
            ToolDescriptor(
                name="music_control",
                description="Điều khiển nhạc thật: stop, pause, resume, next, previous hoặc status.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string",
                                   "enum": ["stop", "pause", "resume", "next",
                                            "previous", "status"]},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=self.control_music,
                timeout_ms=5000,
                read_only=False,
                idempotent=False,
                concurrency_group="music",
            ),
        ]

    async def search_music(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        candidates = await ytdlp_search(
            query, max_results=self._search_results,
            timeout_s=self._resolve_timeout_s)
        self._last_candidates = {
            str(item.get("video_id") or "").strip(): dict(item)
            for item in candidates
            if str(item.get("video_id") or "").strip()
        }
        if not candidates:
            return {"status": "not_found", "candidates": []}
        return {"status": "ok", "candidates": candidates}

    async def play_music(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        video_id = str(arguments.get("video_id") or "").strip()
        query = str(arguments.get("query") or "").strip()
        candidate: Dict[str, Any] = {}

        if video_id:
            candidate = self._last_candidates.get(video_id, {})
        elif query:
            candidates = await ytdlp_search(
                query,
                max_results=self._search_results,
                timeout_s=self._resolve_timeout_s,
            )
            self._last_candidates = {
                str(item.get("video_id") or "").strip(): dict(item)
                for item in candidates
                if str(item.get("video_id") or "").strip()
            }
            if not candidates:
                return {
                    "status": "not_found",
                    "query": query,
                    "video_id": "",
                }
            candidate = dict(candidates[0])
            video_id = str(candidate.get("video_id") or "").strip()
            if not video_id:
                raise ValueError("music search returned a candidate without video_id")
        else:
            raise ValueError("music_play needs query or video_id")

        title = str(candidate.get("title") or "")
        channel = str(candidate.get("channel") or "")
        duration = float(candidate.get("duration_s") or 0.0)
        stream_url = await ytdlp_resolve_url(
            video_id,
            timeout_s=self._resolve_timeout_s,
        )
        http_headers = dict(getattr(stream_url, "http_headers", {}) or {})
        if not title:
            title = query or video_id
        track = MusicTrack(
            video_id=video_id,
            title=title,
            channel=channel,
            duration_s=float(duration or 0),
            stream_url=str(stream_url),
            http_headers=http_headers,
        )
        result = self._player.set_pending(track)
        if query:
            result = {**result, "query": query}
        return result

    async def control_music(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        if action == "stop":
            return await self._player.stop()
        if action == "pause":
            return await self._player.pause()
        if action == "resume":
            return await self._player.resume()
        if action == "next":
            return await self._player.next_track()
        if action == "previous":
            return await self._player.previous_track()
        if action == "status":
            return {"status": "ok", **self._player.status()}
        raise ValueError(f"unknown music action: {action!r}")


def music_descriptors(player: MusicPlayer, **kwargs) -> List[ToolDescriptor]:
    """Convenience factory used by session wiring and tests."""
    return MusicToolProvider(player, **kwargs).descriptors()
