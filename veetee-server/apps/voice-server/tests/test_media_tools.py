from __future__ import annotations

import asyncio

import pytest

from veetee_voice_server.conversation.arbiter import TurnArbiter
from veetee_voice_server.conversation.cancellation import TurnCancelledError
from veetee_voice_server.conversation.types import AudioChunk, OutputKind, WakeSource
from veetee_voice_server.tools.media import (
    MediaCandidate,
    MediaSearchResult,
    MediaToolBroker,
)


class Sink:
    def __init__(self) -> None:
        self.outputs = []
        self.stopped = asyncio.Event()

    async def emit(self, output) -> None:  # type: ignore[no-untyped-def]
        self.outputs.append(output)
        if output.kind is OutputKind.TTS_STOP:
            self.stopped.set()


class BlockingAudioSink(Sink):
    def __init__(self) -> None:
        super().__init__()
        self.audio_started = asyncio.Event()

    async def emit(self, output) -> None:  # type: ignore[no-untyped-def]
        self.outputs.append(output)
        if output.kind is OutputKind.TTS_STOP:
            self.stopped.set()
        if output.kind is OutputKind.AUDIO:
            self.audio_started.set()
            await asyncio.Event().wait()


class Provider:
    def __init__(self, *, cancel_after: bool = False) -> None:
        self.cancel_after = cancel_after
        self.searched = []

    async def search(self, **kwargs):  # type: ignore[no-untyped-def]
        self.searched.append(kwargs)
        kwargs["context"].checkpoint()
        return MediaSearchResult(
            selected=MediaCandidate("track-1", "A song", "An artist", "An album", 180)
        )

    async def stream(self, candidate, context):  # type: ignore[no-untyped-def]
        yield AudioChunk(0, 24_000, "pcm_s16le", b"\x00\x00" * 240)
        if self.cancel_after:
            context.token.cancel("test_abort")
        yield AudioChunk(1, 24_000, "pcm_s16le", b"\x01\x00" * 240)


class ClosingProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.closed = asyncio.Event()

    async def stream(self, candidate, context):  # type: ignore[no-untyped-def]
        try:
            yield AudioChunk(0, 24_000, "pcm_s16le", b"\x00\x00" * 240)
        finally:
            self.closed.set()


class SearchOnlyProvider:
    def __init__(self, result: MediaSearchResult) -> None:
        self.result = result

    async def search(self, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["context"].checkpoint()
        return self.result

    async def stream(self, candidate, context):  # type: ignore[no-untyped-def]
        raise AssertionError("stream must not start without a selected candidate")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_media_play_uses_streaming_operation_and_same_turn_lifecycle() -> None:
    arbiter = TurnArbiter("session-media")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    sink = Sink()
    provider = Provider()
    broker = MediaToolBroker(provider, sink=sink, arbiter=arbiter)

    catalog = broker.list_tools()
    assert catalog[0]["operationClass"] == "streaming"
    result = await broker.call(
        "media.play",
        {"mode": "specific_track", "title": "A song", "artist": "An artist"},
        context,
    )

    assert result["result"]["status"] == "played"
    assert [item.kind for item in sink.outputs] == [
        OutputKind.TTS_START,
        OutputKind.AUDIO,
        OutputKind.AUDIO,
        OutputKind.TTS_STOP,
    ]
    assert sink.outputs[-1].payload["kind"] == "media"
    assert provider.searched[0]["query"] is None


@pytest.mark.asyncio
async def test_media_play_schema_rejects_title_without_artist() -> None:
    arbiter = TurnArbiter("session-media-schema")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    broker = MediaToolBroker(Provider(), sink=Sink(), arbiter=arbiter)

    with pytest.raises(ValueError, match="Invalid server MCP arguments"):
        await broker.call("media.play", {"mode": "specific_track", "title": "A song"}, context)

    with pytest.raises(ValueError, match="Invalid server MCP arguments"):
        await broker.call(
            "media.play",
            {"mode": "any_track", "title": "A song", "artist": "An artist"},
            context,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_result", "expected_status", "expected_count"),
    [
        (MediaSearchResult(), "not_found", 0),
        (
            MediaSearchResult(
                alternatives=tuple(
                    MediaCandidate(f"track-{index}", f"Song {index}", "Artist")
                    for index in range(12)
                )
            ),
            "needs_selection",
            8,
        ),
    ],
)
async def test_media_search_failure_and_ambiguity_are_bounded(
    search_result: MediaSearchResult,
    expected_status: str,
    expected_count: int,
) -> None:
    arbiter = TurnArbiter("session-media-search")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    sink = Sink()
    broker = MediaToolBroker(
        SearchOnlyProvider(search_result), sink=sink, arbiter=arbiter
    )

    result = await broker.call(
        "media.play", {"mode": "any_track", "query": "fixture music"}, context
    )

    assert result["result"]["status"] == expected_status
    assert len(result["result"]["alternatives"]) == expected_count
    assert sink.outputs == []


@pytest.mark.asyncio
async def test_media_stream_bound_fails_before_publishing_audio() -> None:
    arbiter = TurnArbiter("session-media-bound")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    sink = Sink()
    broker = MediaToolBroker(
        Provider(), sink=sink, arbiter=arbiter, max_audio_bytes=8
    )

    with pytest.raises(ValueError, match="exceeded configured bounds"):
        await broker.call(
            "media.play", {"mode": "any_track", "query": "fixture music"}, context
        )
    assert sink.outputs == []


@pytest.mark.asyncio
async def test_media_play_emits_cancelled_stop_and_propagates_abort() -> None:
    arbiter = TurnArbiter("session-media-abort")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    sink = Sink()
    broker = MediaToolBroker(Provider(cancel_after=True), sink=sink, arbiter=arbiter)

    with pytest.raises(TurnCancelledError):
        await broker.call(
            "media.play", {"mode": "any_track", "query": "fixture music"}, context
        )
    await asyncio.wait_for(sink.stopped.wait(), timeout=0.2)
    assert sink.outputs[0].kind is OutputKind.TTS_START
    assert sink.outputs[-1].kind is OutputKind.TTS_STOP
    assert sink.outputs[-1].payload["cancelled"] is True


@pytest.mark.asyncio
async def test_outer_turn_cancellation_closes_provider_while_sink_is_blocked() -> None:
    arbiter = TurnArbiter("session-media-sink-abort")
    await arbiter.open_assistant(WakeSource.BUTTON)
    context = await arbiter.begin_turn(0)
    sink = BlockingAudioSink()
    provider = ClosingProvider()
    broker = MediaToolBroker(provider, sink=sink, arbiter=arbiter)
    call = asyncio.create_task(
        broker.call(
            "media.play",
            {"mode": "any_track", "query": "fixture music"},
            context,
        )
    )
    await sink.audio_started.wait()

    context.token.cancel("button_interrupt")

    with pytest.raises(TurnCancelledError):
        await asyncio.wait_for(call, timeout=0.2)
    await asyncio.wait_for(provider.closed.wait(), timeout=0.2)
    await asyncio.wait_for(sink.stopped.wait(), timeout=0.2)
    assert sink.outputs[-1].kind is OutputKind.TTS_STOP
