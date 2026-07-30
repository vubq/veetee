from __future__ import annotations

import asyncio
import sys
from time import monotonic

import pytest

from veetee_voice_server.conversation.cancellation import (
    CancellationToken,
    OperationContext,
    TurnCancelledError,
)
from veetee_voice_server.providers.youtube_music import (
    YouTubeMusicProvider,
    YouTubeMusicProviderError,
)
from veetee_voice_server.tools.media import MediaCandidate


def test_youtube_music_builds_search_only_from_structured_metadata() -> None:
    assert (
        YouTubeMusicProvider._search_query(
            "specific_track", "Nàng Thơ", "Hoàng Dũng", None
        )
        == "Nàng Thơ Hoàng Dũng"
    )
    assert (
        YouTubeMusicProvider._search_query(
            "any_track", None, None, "nhạc phù hợp với buổi tối"
        )
        == "nhạc phù hợp với buổi tối"
    )
    with pytest.raises(ValueError, match="metadata is incomplete"):
        YouTubeMusicProvider._search_query("any_track", None, None, None)


def test_youtube_music_parses_bounded_provider_ids_and_metadata() -> None:
    candidates = YouTubeMusicProvider._candidates(
        {
            "entries": [
                {
                    "id": "Zzn9-ATB9aU",
                    "title": "  Nàng   Thơ  ",
                    "channel": "Hoàng Dũng",
                    "duration": 300,
                },
                {
                    "id": "Zzn9-ATB9aU",
                    "title": "duplicate",
                    "channel": "duplicate",
                },
                {
                    "id": "https://untrusted.example/audio",
                    "title": "bad id",
                    "channel": "bad id",
                },
            ]
        }
    )

    assert candidates == (
        MediaCandidate("Zzn9-ATB9aU", "Nàng Thơ", "Hoàng Dũng", None, 300),
    )


def test_youtube_music_selects_clear_match_and_returns_ambiguous_versions() -> None:
    clear = (
        MediaCandidate("0SJAzTGh1SE", "Nàng Thơ", "Hoàng Dũng"),
        MediaCandidate("ghgePhzbky0", "Nàng Thơ live", "Another channel"),
    )
    result = YouTubeMusicProvider._select_specific(
        clear, title="Nàng Thơ", artist="Hoàng Dũng"
    )
    assert result.selected == clear[0]
    assert result.alternatives == ()

    ambiguous = (
        MediaCandidate("Zzn9-ATB9aU", "Nàng Thơ", "Hoàng Dũng"),
        MediaCandidate("0SJAzTGh1SE", "Nàng Thơ", "Hoàng Dũng"),
    )
    result = YouTubeMusicProvider._select_specific(
        ambiguous, title="Nàng Thơ", artist="Hoàng Dũng"
    )
    assert result.selected is None
    assert set(result.alternatives) == set(ambiguous)


def test_youtube_music_normalization_is_locale_agnostic() -> None:
    assert YouTubeMusicProvider._normalized("  NÀNG-Thơ! ") == "nang tho"
    assert YouTubeMusicProvider._match_score("Nàng Thơ", "NANG THO official") == 0.96


def test_youtube_music_cookie_file_must_be_private(
    tmp_path, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    cookie = tmp_path / "youtube-cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cookie.chmod(0o644)
    monkeypatch.setattr(
        "veetee_voice_server.providers.youtube_music.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    with pytest.raises(
        YouTubeMusicProviderError, match="youtube_music_cookie_file_permissions"
    ):
        YouTubeMusicProvider(cookie_file=cookie)

    cookie.chmod(0o600)
    assert YouTubeMusicProvider(cookie_file=cookie).authenticated is True


@pytest.mark.asyncio
async def test_youtube_music_cancel_kills_metadata_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SearchProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def terminate(self) -> None:
            return

        async def wait(self) -> int:
            await asyncio.Event().wait()
            return 0

    process = SearchProcess()

    async def spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        "veetee_voice_server.providers.youtube_music.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    provider = YouTubeMusicProvider()
    token = CancellationToken()
    context = OperationContext("session", "turn", 1, token, monotonic() + 5)
    search = asyncio.create_task(
        provider.search(
            mode="any_track",
            title=None,
            artist=None,
            query="fixture music",
            context=context,
        )
    )
    await asyncio.sleep(0)
    token.cancel("button_interrupt")

    with pytest.raises(TurnCancelledError):
        await asyncio.wait_for(search, timeout=0.2)
    assert process.killed is True


@pytest.mark.asyncio
async def test_youtube_music_cancel_kills_signal_resistant_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []
    downloader_command: tuple[object, ...] = ()
    spawn_count = 0

    async def spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal downloader_command, spawn_count
        spawn_count += 1
        if spawn_count == 1:
            downloader_command = args
        program = (
            "import os,signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "chunk=b'\\0'*65536; "
            "exec(\"while True:\\n os.write(1, chunk)\")"
            if spawn_count == 1
            else
            "import os,signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "exec(\"while True:\\n data=os.read(0,65536)\\n "
            "if not data: break\\n os.write(1,data)\")"
        )
        process = await original_spawn(sys.executable, "-c", program, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        "veetee_voice_server.providers.youtube_music.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    provider = YouTubeMusicProvider(process_shutdown_seconds=0.5)
    token = CancellationToken()
    context = OperationContext("session", "turn", 1, token, monotonic() + 5)
    stream = provider.stream(
        MediaCandidate("0SJAzTGh1SE", "Nàng Thơ", "Hoàng Dũng"), context
    )
    try:
        first = await asyncio.wait_for(anext(stream), timeout=1)
        assert first.data
        token.cancel("button_interrupt")
        await asyncio.wait_for(stream.aclose(), timeout=0.4)
        assert len(children) == 2
        assert all(process.returncode is not None for process in children)
        downloader_index = downloader_command.index("--downloader")
        assert downloader_command[downloader_index + 1] == "ffmpeg"
    finally:
        for process in children:
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        (b"HTTP Error 429: Too Many Requests", "youtube_music_rate_limited"),
        (b"Sign in to confirm you are not a bot", "youtube_music_rate_limited"),
        (b"HTTP Error 403: Forbidden", "youtube_music_stream_forbidden"),
        (b"Use --cookies for authentication", "youtube_music_auth_required"),
        (b"some other extractor error", "youtube_music_stream_failed"),
    ],
)
def test_youtube_music_redacts_provider_errors(
    detail: bytes, expected: str
) -> None:
    assert YouTubeMusicProvider._provider_error_code(detail, "stream") == expected
