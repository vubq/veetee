"""Unit tests for M1.6 Fake AI pipeline components, framing, downlink queue, and orchestration."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from veetee_server.audio.codec import (
    DOWNLINK_PCM_FORMAT,
    UPLINK_PCM_FORMAT,
    FakeOpusDecoder,
    FakeOpusEncoder,
)
from veetee_server.audio.pacer import PacketPacer
from veetee_server.audio.protocol import AudioError, parse_audio_frame
from veetee_server.audio.queue import (
    AudioQueueItem,
    OverflowPolicy,
    QueueClosedError,
    SlowClientQueueOverflowError,
)
from veetee_server.device_gateway.downlink import GatewayEventSink, run_downlink_sender
from veetee_server.domain.session import DeviceSession, SessionState, TurnState
from veetee_server.persistence import QuotaCheckResult
from veetee_server.pipeline.asr import FakeASR
from veetee_server.pipeline.downlink import DownlinkItem, DownlinkKind, DownlinkQueue
from veetee_server.pipeline.events import (
    SttEvent,
    TtsChunkEvent,
    TtsSentenceStartEvent,
    TtsStartEvent,
    TtsStopEvent,
)
from veetee_server.pipeline.framing import build_downlink_frame
from veetee_server.pipeline.llm import FakeLLM
from veetee_server.pipeline.orchestrator import FakePipeline, PipelineOutcome
from veetee_server.pipeline.tts import FakeTTS
from veetee_server.pipeline.vad import FakeVAD, VadEventKind, pcm_rms


class RecordingSink:
    """Simple recording sink for pipeline event unit tests."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class RejectingQuotaService:
    def check_only(self, _user_id: Any, metric: str) -> QuotaCheckResult:
        assert metric == "llm_tokens_day"
        return QuotaCheckResult(False, 1, 1, 0, "quota exceeded")

    def check_and_consume(
        self, _user_id: Any, _metric: str, _amount: int
    ) -> QuotaCheckResult:
        raise AssertionError("TTS quota must not run after LLM precheck rejection")

    def record_usage(self, _user_id: Any, _metric: str, _amount: int) -> None:
        raise AssertionError("Usage must not be recorded after LLM precheck rejection")


def _make_speech_pcm(duration_ms: float = 60.0, amplitude: int = 1000) -> bytes:
    sample_count = int(16000 * (duration_ms / 1000.0))
    pattern = amplitude.to_bytes(2, byteorder="little", signed=True)
    return pattern * sample_count


def _make_silence_pcm(duration_ms: float = 60.0) -> bytes:
    sample_count = int(16000 * (duration_ms / 1000.0))
    return b"\x00\x00" * sample_count


# --- FakeVAD Unit Tests ---


def test_pcm_rms_calculation() -> None:
    assert pcm_rms(b"") == 0.0
    assert pcm_rms(_make_silence_pcm()) == 0.0
    speech = _make_speech_pcm(amplitude=5000)
    assert pytest.approx(pcm_rms(speech), rel=1e-2) == 5000.0


def test_fake_vad_init_validation() -> None:
    with pytest.raises(ValueError, match="speech_threshold"):
        FakeVAD(speech_threshold=-1.0)
    with pytest.raises(ValueError, match="start_frames"):
        FakeVAD(start_frames=0)
    with pytest.raises(ValueError, match="end_silence_frames"):
        FakeVAD(end_silence_frames=0)
    with pytest.raises(ValueError, match="max_utterance_frames"):
        FakeVAD(start_frames=5, max_utterance_frames=2)


def test_fake_vad_speech_detection_and_boundaries() -> None:
    vad = FakeVAD(start_frames=2, end_silence_frames=2, speech_threshold=100.0)
    silence = _make_silence_pcm()
    speech = _make_speech_pcm(amplitude=1000)

    # Frame 0: silence -> idle
    e0 = vad.process_frame(silence)
    assert e0.kind == VadEventKind.SILENCE

    # Frame 1: speech -> counting
    e1 = vad.process_frame(speech)
    assert e1.kind == VadEventKind.PROCESSING

    # Frame 2: speech -> SPEECH_START (start_frames=2)
    e2 = vad.process_frame(speech)
    assert e2.kind == VadEventKind.SPEECH_START

    # Frame 3: speech -> processing
    e3 = vad.process_frame(speech)
    assert e3.kind == VadEventKind.PROCESSING

    # Frame 4: silence -> 1/2 silence
    e4 = vad.process_frame(silence)
    assert e4.kind == VadEventKind.PROCESSING

    # Frame 5: silence -> 2/2 silence -> SPEECH_END
    e5 = vad.process_frame(silence)
    assert e5.kind == VadEventKind.SPEECH_END

    segment = vad.finish()
    assert segment is not None
    assert segment.start_frame_index == 1
    assert segment.end_frame_index == 4


def test_fake_vad_no_speech_returns_none() -> None:
    vad = FakeVAD(start_frames=2, end_silence_frames=2)
    silence = _make_silence_pcm()
    for _ in range(5):
        vad.process_frame(silence)
    assert vad.finish() is None


def test_fake_vad_max_utterance_frames() -> None:
    vad = FakeVAD(start_frames=1, end_silence_frames=10, max_utterance_frames=3)
    speech = _make_speech_pcm()

    assert vad.process_frame(speech).kind == VadEventKind.SPEECH_START
    vad.process_frame(speech)  # f1: processing
    assert vad.process_frame(speech).kind == VadEventKind.SPEECH_END

    segment = vad.finish()
    assert segment is not None
    assert segment.start_frame_index == 0
    assert segment.end_frame_index == 3


def test_fake_vad_reset() -> None:
    vad = FakeVAD(start_frames=1)
    vad.process_frame(_make_speech_pcm())
    vad.reset()
    assert vad.finish() is None


# --- FakeASR Unit Tests ---


def test_fake_asr_transcribe_default_and_fingerprint() -> None:
    asr = FakeASR(default_text="Xin chào", fingerprints={"aabb": "Pin transcript"})
    assert asr.transcribe(b"unknown pcm") == "Xin chào"
    import hashlib

    target_pcm = b"target pcm"
    fp = hashlib.sha256(target_pcm).hexdigest()
    mapped_asr = FakeASR(default_text="Default", fingerprints={fp: "Mapped text"})
    assert mapped_asr.transcribe(target_pcm) == "Mapped text"


def test_fake_asr_empty_default_text_raises() -> None:
    with pytest.raises(ValueError, match="default_text"):
        FakeASR(default_text="   ")


# --- FakeLLM Unit Tests ---


def test_fake_llm_segmentation() -> None:
    llm = FakeLLM()
    assert llm.segments("Câu 1. Câu 2! Câu 3?") == ["Câu 1.", "Câu 2!", "Câu 3?"]
    assert llm.segments("Một câu duy nhất không dấu ngắt") == [
        "Một câu duy nhất không dấu ngắt"
    ]


# --- FakeTTS Unit Tests ---


@pytest.mark.asyncio
async def test_fake_tts_synthesis() -> None:
    tts = FakeTTS(chunks_per_sentence=2, delay_seconds=0.0)
    chunks = [chunk async for chunk in tts.synthesize("Xin chào.")]
    assert len(chunks) == 2
    # 24000 Hz * 2 bytes * 0.06s = 2880 bytes
    assert len(chunks[0]) == 2880
    # Deterministic output
    chunks_again = [chunk async for chunk in tts.synthesize("Xin chào.")]
    assert chunks == chunks_again


def test_fake_tts_init_validation() -> None:
    with pytest.raises(ValueError):
        FakeTTS(chunks_per_sentence=0)
    with pytest.raises(ValueError):
        FakeTTS(delay_seconds=-1.0)


# --- Framing v1/v2/v3 Unit Tests ---


def test_build_downlink_frame_v1_v2_v3() -> None:
    opus = b"\xf8\xff\xfe\x01\x02"

    # v1
    f1 = build_downlink_frame(1, opus)
    assert f1 == opus

    # v2
    f2 = build_downlink_frame(2, opus, timestamp_ms=1000)
    assert len(f2) == 16 + len(opus)
    pkt2 = parse_audio_frame(f2, negotiated_version=2, max_payload_bytes=1024)
    assert pkt2.payload == opus
    assert pkt2.timestamp_ms == 1000

    # v3
    f3 = build_downlink_frame(3, opus)
    assert len(f3) == 4 + len(opus)
    pkt3 = parse_audio_frame(f3, negotiated_version=3, max_payload_bytes=1024)
    assert pkt3.payload == opus
    assert pkt3.timestamp_ms is None

    wrapped_v2 = build_downlink_frame(2, opus, now_ms=lambda: 0x1_0000_0001)
    assert parse_audio_frame(wrapped_v2, negotiated_version=2).timestamp_ms == 1


def test_build_downlink_frame_errors() -> None:
    with pytest.raises(AudioError, match="empty Opus payload"):
        build_downlink_frame(1, b"")
    with pytest.raises(AudioError, match="Unsupported protocol version"):
        build_downlink_frame(4, b"opus")


# --- DownlinkQueue Unit Tests ---


@pytest.mark.asyncio
async def test_downlink_queue_fifo_order_and_drain() -> None:
    q = DownlinkQueue(max_items=10)
    c1 = DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"c1", generation=0)
    a1 = DownlinkItem(kind=DownlinkKind.AUDIO, payload=b"a1", generation=0, duration_ms=60.0)

    await q.put(c1)
    await q.put(a1)

    assert q.item_count == 2
    assert (await q.get()) == c1
    assert (await q.get()) == a1


@pytest.mark.asyncio
async def test_downlink_queue_generation_purge() -> None:
    q = DownlinkQueue(max_items=10)
    await q.put(DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"old1", generation=0))
    await q.put(
        DownlinkItem(
            kind=DownlinkKind.AUDIO,
            payload=b"old2",
            generation=0,
            duration_ms=60.0,
        )
    )

    # Bump generation
    q.set_generation(1)
    assert q.item_count == 0

    # Attempting to put stale item is dropped silently
    await q.put(DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"stale", generation=0))
    assert q.item_count == 0

    # Valid current generation put is accepted
    await q.put(DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"new", generation=1))
    assert q.item_count == 1


@pytest.mark.asyncio
async def test_gateway_sink_never_relabels_stale_events() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    sink = GatewayEventSink(session, generation=session.egress_queue.generation)

    session.egress_queue.set_generation(session.egress_queue.generation + 1)
    await sink.emit(SttEvent(text="stale", session_id=str(session.id)))

    assert session.egress_queue.item_count == 0


@pytest.mark.asyncio
async def test_sender_drops_dequeued_audio_when_barge_in_resets_pacer() -> None:
    sleep_started = asyncio.Event()

    async def blocking_sleep(_: float) -> None:
        sleep_started.set()
        await asyncio.Future()

    class RecordingWebSocket:
        def __init__(self) -> None:
            self.binary: list[bytes] = []

        async def send_bytes(self, payload: bytes) -> None:
            self.binary.append(payload)

        async def send_text(self, payload: str) -> None:
            pass

    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    session.pacer = PacketPacer(clock=lambda: 0.0, sleeper=blocking_sleep)
    generation = session.egress_queue.generation
    for payload in (b"first", b"stale"):
        await session.egress_queue.put(
            DownlinkItem(
                kind=DownlinkKind.AUDIO,
                payload=payload,
                generation=generation,
                duration_ms=60.0,
            )
        )
    websocket = RecordingWebSocket()
    sender = asyncio.create_task(
        run_downlink_sender(session, websocket, playback_grace_seconds=3.0)  # type: ignore[arg-type]
    )
    await sleep_started.wait()

    await session.abort_turn()
    await asyncio.sleep(0)
    session.egress_queue.close()
    await sender

    assert websocket.binary == [b"first"]


@pytest.mark.asyncio
async def test_downlink_queue_overflow_fail_session() -> None:
    q = DownlinkQueue(max_items=1, overflow_policy=OverflowPolicy.FAIL_SESSION)
    await q.put(DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"item1", generation=0))

    with pytest.raises(SlowClientQueueOverflowError):
        await q.put(DownlinkItem(kind=DownlinkKind.CONTROL, payload=b"item2", generation=0))


@pytest.mark.asyncio
async def test_tts_stop_marks_activity_when_sent() -> None:
    class RecordingWebSocket:
        async def send_text(self, payload: str) -> None:
            self.payload = payload

    session = DeviceSession(device_id="d1", client_id="c1")
    before = session.last_conversation_activity
    payload = b'{"type":"tts","state":"stop"}'
    await session.egress_queue.put(
        DownlinkItem(kind=DownlinkKind.CONTROL, payload=payload, generation=0)
    )
    websocket = RecordingWebSocket()
    sender = asyncio.create_task(
        run_downlink_sender(session, websocket, playback_grace_seconds=3.0)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    session.egress_queue.close()
    await sender

    assert websocket.payload == payload.decode()
    assert session.last_conversation_activity >= before + 3.0


@pytest.mark.asyncio
async def test_downlink_queue_close_wakes_waiter() -> None:
    q = DownlinkQueue()

    async def waiter() -> None:
        with pytest.raises(QueueClosedError):
            await q.get()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)
    q.close()
    await task


# --- FakePipeline Unit Tests ---


@pytest.mark.asyncio
async def test_fake_pipeline_event_order_and_completion() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    turn = await session.start_turn()

    # Enqueue speech PCM frames while LISTENING
    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    pcm = _make_speech_pcm(amplitude=1000)
    opus = encoder.encode(pcm)

    # 3 speech frames + 3 silence frames
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    silence_opus = encoder.encode(_make_silence_pcm())
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=silence_opus, generation=session.ingress_queue.generation)
        )

    session.begin_processing()

    pipeline = FakePipeline(
        decoder=decoder,
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Thử nghiệm."),
        llm=FakeLLM(),
        tts=FakeTTS(chunks_per_sentence=2, delay_seconds=0.0),
    )

    sink = RecordingSink()
    outcome = await pipeline.run(session, sink)

    assert outcome == PipelineOutcome.COMPLETED
    assert turn.state == TurnState.COMPLETED
    assert session.state == SessionState.IDLE

    # Event types order check
    types = [type(e) for e in sink.events]
    assert types == [
        SttEvent,
        TtsStartEvent,
        TtsSentenceStartEvent,
        TtsChunkEvent,
        TtsChunkEvent,
        TtsStopEvent,
    ]
    stt_event = sink.events[0]
    assert isinstance(stt_event, SttEvent)
    assert stt_event.text == "Thử nghiệm."


@pytest.mark.asyncio
async def test_fake_pipeline_rejects_llm_quota_before_provider_call() -> None:
    session = DeviceSession(device_id="quota-device", client_id="quota-client")
    session.owner_user_id = uuid4()
    session.accept()
    await session.start_turn()
    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    for pcm in [*([_make_speech_pcm()] * 3), *([_make_silence_pcm()] * 3)]:
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(pcm),
                generation=session.ingress_queue.generation,
            )
        )
    session.begin_processing()
    llm = FakeLLM()
    pipeline = FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Quota test."),
        llm=llm,
        tts=FakeTTS(),
        quota_service=RejectingQuotaService(),  # type: ignore[arg-type]
    )
    sink = RecordingSink()
    outcome = await pipeline.run(session, sink)
    assert outcome is PipelineOutcome.QUOTA_EXCEEDED
    assert [type(event) for event in sink.events] == [SttEvent]


@pytest.mark.asyncio
async def test_fake_pipeline_no_utterance() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    await session.start_turn()
    session.begin_processing()

    pipeline = FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(),
        asr=FakeASR(),
        llm=FakeLLM(),
        tts=FakeTTS(),
    )

    sink = RecordingSink()
    outcome = await pipeline.run(session, sink)

    assert outcome == PipelineOutcome.NO_UTTERANCE
    assert len(sink.events) == 0


@pytest.mark.asyncio
async def test_fake_pipeline_cancellation_and_stale_suppression() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    turn = await session.start_turn()

    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    pcm = _make_speech_pcm(amplitude=1000)
    opus = encoder.encode(pcm)
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(_make_silence_pcm()),
                generation=session.ingress_queue.generation,
            )
        )

    session.begin_processing()

    # Delayed TTS allows cancelling mid-stream
    pipeline = FakePipeline(
        decoder=decoder,
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(),
        llm=FakeLLM(),
        tts=FakeTTS(chunks_per_sentence=3, delay_seconds=0.05),
    )

    sink = RecordingSink()

    async def abort_after_start() -> None:
        await asyncio.sleep(0.02)
        await session.abort_turn()

    abort_task = asyncio.create_task(abort_after_start())
    outcome = await pipeline.run(session, sink)
    await abort_task

    assert outcome == PipelineOutcome.CANCELLED
    assert turn.state == TurnState.ABORTED


class StreamingMockLLM:
    """Mock LLM that streams custom deltas with delays to test pipeline timing."""

    def __init__(self, deltas: list[tuple[float, str]]) -> None:
        self.deltas = deltas
        self.stream_closed = False

    def generate_stream(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        class StreamIter:
            def __init__(self, outer: StreamingMockLLM) -> None:
                self.outer = outer
                self.index = 0

            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                if self.index >= len(self.outer.deltas):
                    raise StopAsyncIteration
                delay, text = self.outer.deltas[self.index]
                self.index += 1
                if delay > 0:
                    await asyncio.sleep(delay)
                from veetee_server.pipeline.llm import LLMTextDeltaEvent
                return LLMTextDeltaEvent(delta=text, index=self.index)

            async def aclose(self) -> None:
                self.outer.stream_closed = True

        return StreamIter(self)


class FailingStreamingMockLLM(StreamingMockLLM):
    def generate_stream(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        outer = self

        async def stream() -> Any:
            try:
                raise RuntimeError("provider unavailable")
                yield
            finally:
                outer.stream_closed = True

        return stream()


@pytest.mark.asyncio
async def test_pipeline_first_audio_starts_before_llm_completion() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    await session.start_turn()

    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    pcm = _make_speech_pcm(amplitude=1000)
    opus = encoder.encode(pcm)
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(_make_silence_pcm()),
                generation=session.ingress_queue.generation,
            )
        )
    session.begin_processing()

    # LLM yields sentence 1, delays 0.15s, then yields sentence 2
    mock_llm = StreamingMockLLM([
        (0.0, "Xin chào quý khách, rất vui được gặp bạn! "),
        (0.15, "Hôm nay tôi có thể giúp gì cho bạn?"),
    ])

    pipeline = FakePipeline(
        decoder=decoder,
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Alo"),
        llm=mock_llm,
        tts=FakeTTS(chunks_per_sentence=2, delay_seconds=0.01),
    )

    sink = RecordingSink()
    pipeline_task = asyncio.create_task(pipeline.run(session, sink))
    await asyncio.sleep(0.05)

    assert not pipeline_task.done()
    assert any(isinstance(event, TtsChunkEvent) for event in sink.events)

    outcome = await pipeline_task

    assert outcome == PipelineOutcome.COMPLETED
    # Check that TtsStartEvent and first TtsSentenceStartEvent happened for sentence 1
    tts_starts = [e for e in sink.events if isinstance(e, TtsSentenceStartEvent)]
    assert len(tts_starts) == 2
    assert tts_starts[0].text == "Xin chào quý khách, rất vui được gặp bạn!"
    assert "giúp gì cho bạn" in tts_starts[1].text


@pytest.mark.asyncio
async def test_pipeline_max_wait_flush_on_stalled_llm_delta() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    await session.start_turn()

    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    pcm = _make_speech_pcm(amplitude=1000)
    opus = encoder.encode(pcm)
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(_make_silence_pcm()),
                generation=session.ingress_queue.generation,
            )
        )
    session.begin_processing()

    # Delta 1 is incomplete (no terminal punct). Second delta stalls for 0.4s (> max_wait 0.35s)
    mock_llm = StreamingMockLLM([
        (0.0, "Hôm nay tôi muốn nói với bạn một điều rất quan trọng "),
        (0.40, "đó là hãy giữ gìn sức khỏe."),
    ])

    from veetee_server.pipeline.segmenter import TTSSegmenterConfig, TTSTokenSegmenter
    config = TTSSegmenterConfig(first_min_chars=20, min_chars=30, max_wait_seconds=0.15)

    pipeline = FakePipeline(
        decoder=decoder,
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Alo"),
        llm=mock_llm,
        tts=FakeTTS(chunks_per_sentence=2, delay_seconds=0.01),
        segmenter=TTSTokenSegmenter(config=config),
    )

    sink = RecordingSink()
    outcome = await pipeline.run(session, sink)

    assert outcome == PipelineOutcome.COMPLETED
    tts_starts = [e for e in sink.events if isinstance(e, TtsSentenceStartEvent)]
    assert len(tts_starts) == 2
    assert "một điều rất quan trọng" in tts_starts[0].text


@pytest.mark.asyncio
async def test_pipeline_llm_stream_cancellation_closes_stream() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    await session.start_turn()

    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT)
    pcm = _make_speech_pcm(amplitude=1000)
    opus = encoder.encode(pcm)
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(_make_silence_pcm()),
                generation=session.ingress_queue.generation,
            )
        )
    session.begin_processing()

    # Long streaming deltas with delay
    mock_llm = StreamingMockLLM([
        (0.0, "Câu thứ nhất dài để ngắt segment. "),
        (0.05, "Câu thứ hai tiếp tục xuất hiện. "),
        (0.05, "Câu thứ ba tiếp tục xuất hiện. "),
    ])

    pipeline = FakePipeline(
        decoder=decoder,
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Alo"),
        llm=mock_llm,
        tts=FakeTTS(chunks_per_sentence=5, delay_seconds=0.05),
    )

    sink = RecordingSink()

    async def abort_task() -> None:
        await asyncio.sleep(0.03)
        await session.abort_turn()

    task = asyncio.create_task(abort_task())
    outcome = await pipeline.run(session, sink)
    await task

    assert outcome == PipelineOutcome.CANCELLED
    assert mock_llm.stream_closed is True


@pytest.mark.asyncio
async def test_pipeline_propagates_llm_failure_and_closes_stream() -> None:
    session = DeviceSession(device_id="d1", client_id="c1")
    session.accept()
    await session.start_turn()

    encoder = FakeOpusEncoder(pcm_format=UPLINK_PCM_FORMAT)
    opus = encoder.encode(_make_speech_pcm())
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(payload=opus, generation=session.ingress_queue.generation)
        )
    for _ in range(3):
        await session.ingress_queue.put(
            AudioQueueItem(
                payload=encoder.encode(_make_silence_pcm()),
                generation=session.ingress_queue.generation,
            )
        )
    session.begin_processing()
    mock_llm = FailingStreamingMockLLM([])
    pipeline = FakePipeline(
        decoder=FakeOpusDecoder(pcm_format=UPLINK_PCM_FORMAT),
        encoder=FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT),
        protocol_version=1,
        vad=FakeVAD(start_frames=2, end_silence_frames=2),
        asr=FakeASR(default_text="Alo"),
        llm=mock_llm,
        tts=FakeTTS(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await pipeline.run(session, RecordingSink())

    assert mock_llm.stream_closed is True
