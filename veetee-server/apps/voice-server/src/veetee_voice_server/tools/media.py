from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol

from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import OperationContext
from veetee_voice_server.conversation.types import AudioChunk, ConversationOutput, OutputKind
from veetee_voice_server.providers.tools import RegistryToolBroker, ToolSpec
from veetee_voice_server.transport.sink import ConversationSink


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    """Provider-owned identity; no URL or executable data crosses the tool boundary."""

    provider_item_id: str
    title: str
    artist: str
    album: str | None = None
    duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class MediaSearchResult:
    selected: MediaCandidate | None = None
    alternatives: tuple[MediaCandidate, ...] = ()


class MediaProvider(Protocol):
    async def search(
        self,
        *,
        mode: str,
        title: str | None,
        artist: str | None,
        query: str | None,
        context: OperationContext,
    ) -> MediaSearchResult: ...

    def stream(
        self, candidate: MediaCandidate, context: OperationContext
    ) -> AsyncGenerator[AudioChunk, None]: ...


_MEDIA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode"],
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["specific_track", "any_track"],
        },
        "title": {"type": "string", "minLength": 1, "maxLength": 240},
        "artist": {"type": "string", "minLength": 1, "maxLength": 240},
        "query": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "allOf": [
        {
            "if": {"properties": {"mode": {"const": "specific_track"}}},
            "then": {"required": ["title", "artist"]},
        },
        {
            "if": {"properties": {"mode": {"const": "any_track"}}},
            "then": {
                "required": ["query"],
                "not": {"required": ["title", "artist"]},
            },
        },
    ],
}


def _candidate_payload(candidate: MediaCandidate) -> dict[str, Any]:
    return {
        "provider_item_id": candidate.provider_item_id,
        "title": candidate.title,
        "artist": candidate.artist,
        "album": candidate.album,
        "duration_seconds": candidate.duration_seconds,
    }


class MediaToolBroker(RegistryToolBroker):
    """Session-scoped media playback with the normal turn cancellation scope."""

    def __init__(
        self,
        provider: MediaProvider,
        *,
        sink: ConversationSink,
        arbiter: TurnArbiter,
        max_audio_chunks: int = 120_000,
        max_audio_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        if max_audio_chunks < 1 or max_audio_bytes < 1:
            raise ValueError("Media stream bounds must be positive")
        self._provider = provider
        self._sink = sink
        self._arbiter = arbiter
        self._max_audio_chunks = max_audio_chunks
        self._max_audio_bytes = max_audio_bytes

        async def play(arguments: dict[str, Any], context: OperationContext) -> dict[str, Any]:
            return await self._play(arguments, context)

        super().__init__(
            [
                ToolSpec(
                    name="media.play",
                    description=(
                        "Search and play a provider-authorized music track. "
                        "Use specific_track only when both title and artist are known; "
                        "use any_track with an AI-selected contextual search query when "
                        "the user accepts a provider-selected result. "
                        "The provider owns search and streaming; URLs are never accepted."
                    ),
                    input_schema=_MEDIA_INPUT_SCHEMA,
                    handler=play,
                    operation_class="streaming",
                )
            ]
        )

    async def _play(
        self, arguments: dict[str, Any], context: OperationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        mode = arguments["mode"]
        title = arguments.get("title")
        artist = arguments.get("artist")
        query = arguments.get("query")
        result = await self._provider.search(
            mode=mode,
            title=title if isinstance(title, str) else None,
            artist=artist if isinstance(artist, str) else None,
            query=query if isinstance(query, str) else None,
            context=context,
        )
        context.checkpoint()
        if result.selected is None:
            return {
                "status": "not_found" if not result.alternatives else "needs_selection",
                "alternatives": [
                    _candidate_payload(item) for item in result.alternatives[:8]
                ],
            }

        candidate = result.selected
        started = False
        chunk_count = 0
        byte_count = 0
        stream = self._provider.stream(candidate, context)
        try:
            try:
                async for audio in stream:
                    context.checkpoint()
                    if audio.encoding != "pcm_s16le" or not audio.data:
                        raise ValueError("Media provider returned unsupported audio")
                    chunk_count += 1
                    byte_count += len(audio.data)
                    if (
                        chunk_count > self._max_audio_chunks
                        or byte_count > self._max_audio_bytes
                    ):
                        raise ValueError("Media stream exceeded configured bounds")
                    if not started:
                        await self._arbiter.mark_speaking(context)
                        await self._sink.emit(
                            ConversationOutput(
                                kind=OutputKind.TTS_START,
                                turn_id=context.turn_id,
                                generation=context.generation,
                                payload={
                                    "kind": "media",
                                    "title": candidate.title,
                                    "artist": candidate.artist,
                                },
                            )
                        )
                        started = True
                    await self._sink.emit(
                        ConversationOutput(
                            kind=OutputKind.AUDIO,
                            turn_id=context.turn_id,
                            generation=context.generation,
                            payload={
                                "kind": "media",
                                "title": candidate.title,
                                "artist": candidate.artist,
                            },
                            audio=audio,
                        )
                    )
            finally:
                await self._close_stream(stream)
            context.checkpoint()
            if not started:
                return {
                    "status": "stream_empty",
                    "track": _candidate_payload(candidate),
                }
            return {
                "status": "played",
                "track": _candidate_payload(candidate),
                "audio_chunks": chunk_count,
            }
        finally:
            if started:
                cancelled = context.token.cancelled
                await self._sink.emit(
                    ConversationOutput(
                        kind=OutputKind.TTS_STOP,
                        turn_id=context.turn_id,
                        generation=context.generation,
                        payload={"kind": "media", "cancelled": cancelled},
                    )
                )

    @staticmethod
    async def _close_stream(stream: AsyncGenerator[AudioChunk, None]) -> None:
        close_task = asyncio.create_task(stream.aclose())
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            await close_task
            raise


__all__ = ["MediaCandidate", "MediaProvider", "MediaSearchResult", "MediaToolBroker"]
