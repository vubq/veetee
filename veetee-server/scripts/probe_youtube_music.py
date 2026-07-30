#!/usr/bin/env python3
"""Probe YouTube Music metadata and bounded PCM decode without writing media files."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic

from veetee_voice_server.config import Settings
from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.providers.youtube_music import (
    YouTubeMusicProvider,
    YouTubeMusicProviderError,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title")
    parser.add_argument("--artist")
    parser.add_argument("--query")
    parser.add_argument("--decode-seconds", type=float, default=5.0)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--accept-first", action="store_true")
    result = parser.parse_args()
    specific = bool(result.title or result.artist)
    if specific and not (result.title and result.artist):
        parser.error("--title and --artist must be supplied together")
    if specific == bool(result.query):
        parser.error("supply either --title/--artist or --query")
    if not 0.5 <= result.decode_seconds <= 30.0:
        parser.error("--decode-seconds must be between 0.5 and 30")
    return result


async def probe(options: argparse.Namespace) -> int:
    settings = Settings()
    provider = YouTubeMusicProvider(
        output_sample_rate=settings.wire_sample_rate,
        search_results=settings.media_search_results,
        search_seconds=settings.media_search_seconds,
        pcm_chunk_ms=settings.media_pcm_chunk_ms,
        ffmpeg_binary=settings.media_ffmpeg_binary,
        process_shutdown_seconds=settings.media_process_shutdown_seconds,
        cookie_file=(
            Path(settings.media_youtube_cookie_file)
            if settings.media_youtube_cookie_file
            else None
        ),
    )
    token = CancellationToken()
    context = OperationContext(
        "youtube-music-probe",
        "youtube-music-probe:1",
        0,
        token,
        monotonic() + 45.0,
    )
    mode = "specific_track" if options.title else "any_track"
    result = await provider.search(
        mode=mode,
        title=options.title,
        artist=options.artist,
        query=options.query,
        context=context,
    )
    candidate = result.selected
    if candidate is None and options.accept_first and result.alternatives:
        candidate = result.alternatives[0]
    payload: dict[str, object] = {
        "status": (
            "selected"
            if candidate is not None
            else "needs_selection"
            if result.alternatives
            else "not_found"
        ),
        "alternatives": [
            {
                "provider_item_id": item.provider_item_id,
                "title": item.title,
                "artist": item.artist,
            }
            for item in result.alternatives
        ],
    }
    if candidate is None or options.metadata_only:
        if candidate is not None:
            payload["track"] = {
                "provider_item_id": candidate.provider_item_id,
                "title": candidate.title,
                "artist": candidate.artist,
            }
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if candidate is not None or result.alternatives else 2

    target_bytes = round(options.decode_seconds * settings.wire_sample_rate * 2)
    pcm_bytes = 0
    pcm_chunks = 0
    stream = provider.stream(candidate, context)
    try:
        async for audio in stream:
            pcm_chunks += 1
            pcm_bytes += len(audio.data)
            if pcm_bytes >= target_bytes:
                break
    finally:
        await stream.aclose()
    payload.update(
        status="decoded",
        track={
            "provider_item_id": candidate.provider_item_id,
            "title": candidate.title,
            "artist": candidate.artist,
        },
        pcm_chunks=pcm_chunks,
        pcm_bytes=pcm_bytes,
        sample_rate=settings.wire_sample_rate,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> None:
    try:
        code = asyncio.run(probe(arguments()))
    except YouTubeMusicProviderError as error:
        print(json.dumps({"status": "error", "code": error.code}))
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
