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
import shutil
import sys
from typing import Any, Dict, List, Optional

from core.music_player import MusicPlayer, MusicTrack
from core.tools.base import ToolDescriptor

logger = logging.getLogger("MusicTools")


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
    """Resolve a YouTube video id to a direct audio stream URL."""
    video_id = (video_id or "").strip()
    if not video_id:
        raise ValueError("video_id must not be empty")
    watch_url = video_id if video_id.startswith("http") else \
        f"https://www.youtube.com/watch?v={video_id}"
    cmd = _ytdlp_base() + [
        "-g", "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "--no-warnings", "--socket-timeout", "10", watch_url,
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
    url = raw.decode("utf-8", "ignore").strip().splitlines()
    url = url[0].strip() if url else ""
    if not url.startswith("http"):
        raise RuntimeError("music resolve produced no stream URL")
    return url


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
                name="music_search",
                description="Tìm bài hát trên YouTube khi cần lấy danh sách ứng viên (video_id, tiêu đề, kênh). Tool chỉ đọc.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 200},
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
                name="music_play",
                description=(
                    "Phát đúng bài đã chọn bằng video_id. Khi người dùng chỉ nói tên bài/ca sĩ/thể loại, "
                    "hãy gọi music_search trước, tự chọn ứng viên phù hợp từ receipt dựa trên toàn bộ context "
                    "(hoặc hỏi lại nếu mơ hồ), rồi gọi music_play với đúng video_id. "
                    "BẮT BUỘC gọi tool này để thực sự phát nhạc; cấm chỉ nói suông."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "video_id": {"type": "string", "minLength": 1, "maxLength": 64},
                    },
                    "required": ["video_id"],
                    "additionalProperties": False,
                },
                handler=self.play_music,
                timeout_ms=30000,
                read_only=False,
                idempotent=False,
                concurrency_group="music",
            ),
            ToolDescriptor(
                name="music_control",
                description="BẮT BUỘC gọi tool này khi người dùng muốn điều khiển nhạc: action='stop' (dừng/tắt/thôi/im), 'pause' (tạm dừng), 'resume' (tiếp tục), 'next' (bài sau/khác), 'previous' (bài trước), 'status' (trạng thái). Cấm chỉ nói suông mà không gọi tool.",
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
        if not video_id:
            raise ValueError("play_music needs an exact video_id; call music_search first when needed")
        candidate = self._last_candidates.get(video_id, {})
        title = str(candidate.get("title") or "")
        channel = str(candidate.get("channel") or "")
        duration = float(candidate.get("duration_s") or 0.0)
        stream_url = await ytdlp_resolve_url(
            video_id, timeout_s=self._resolve_timeout_s)
        if not title:
            title = video_id
        track = MusicTrack(video_id=video_id, title=title, channel=channel,
                           duration_s=float(duration or 0),
                           stream_url=stream_url)
        return self._player.set_pending(track)

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
