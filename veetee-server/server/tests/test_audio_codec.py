"""Tests for Veetee audio PCM formats, fake Opus codec and deferred native boundaries (M1.5)."""

import sys
from array import array
from dataclasses import FrozenInstanceError

import pytest

from veetee_server.audio import (
    DOWNLINK_PCM_FORMAT,
    UPLINK_PCM_FORMAT,
    CodecError,
    DeferredNativeAudioDecoder,
    DeferredNativeAudioEncoder,
    DeferredNativeAudioResampler,
    DeferredNativeCodecError,
    DeferredNativeResamplerError,
    FakeOpusDecoder,
    FakeOpusEncoder,
    InvalidPCMFormatError,
    NativeOpusDecoder,
    NativeOpusEncoder,
    PCMFormat,
    build_opus_decoder,
    build_opus_encoder,
    is_native_opus_available,
)
from veetee_server.audio.native_opus import MAX_OPUS_PAYLOAD_BYTES
from veetee_server.config import Settings


def test_pcm_format_expected_bytes() -> None:
    # 16 kHz * 60 ms = 960 samples; mono 16-bit => 1920 bytes.
    assert UPLINK_PCM_FORMAT.expected_samples == 960
    assert UPLINK_PCM_FORMAT.expected_bytes == 1920
    # 24 kHz * 60 ms = 1440 samples; mono 16-bit => 2880 bytes.
    assert DOWNLINK_PCM_FORMAT.expected_samples == 1440
    assert DOWNLINK_PCM_FORMAT.expected_bytes == 2880


def test_pcm_format_validate_buffer() -> None:
    UPLINK_PCM_FORMAT.validate_buffer(b"\x00" * 1920)
    DOWNLINK_PCM_FORMAT.validate_buffer(b"\x00" * 2880)
    with pytest.raises(InvalidPCMFormatError):
        UPLINK_PCM_FORMAT.validate_buffer(b"\x00" * 1919)
    with pytest.raises(InvalidPCMFormatError):
        DOWNLINK_PCM_FORMAT.validate_buffer(b"")


def test_pcm_format_is_frozen() -> None:
    fmt = PCMFormat(sample_rate=8000, channels=1, sample_width_bytes=2, frame_duration_ms=20)
    with pytest.raises(FrozenInstanceError):
        fmt.sample_rate = 16000  # type: ignore[misc]


def test_fake_encoder_decoder_roundtrip() -> None:
    encoder = FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT)
    decoder = FakeOpusDecoder(pcm_format=DOWNLINK_PCM_FORMAT)
    pcm = bytes(range(256)) * (DOWNLINK_PCM_FORMAT.expected_bytes // 256 + 1)
    pcm = pcm[: DOWNLINK_PCM_FORMAT.expected_bytes]

    opus = encoder.encode(pcm)
    assert opus[:3] == b"\xf8\xff\xfe"
    decoded = decoder.decode(opus)
    assert len(decoded) == DOWNLINK_PCM_FORMAT.expected_bytes
    # Deterministic: identical input -> identical output.
    assert decoder.decode(opus) == decoded


def test_fake_encoder_rejects_wrong_pcm_size() -> None:
    encoder = FakeOpusEncoder(pcm_format=DOWNLINK_PCM_FORMAT)
    with pytest.raises(InvalidPCMFormatError):
        encoder.encode(b"\x00" * 100)


def test_fake_decoder_rejects_empty_payload() -> None:
    decoder = FakeOpusDecoder()
    with pytest.raises(CodecError):
        decoder.decode(b"")


def test_deferred_native_decoder_raises() -> None:
    decoder = DeferredNativeAudioDecoder()
    with pytest.raises(DeferredNativeCodecError):
        decoder.decode(b"\xf8\xff\xfe")


def test_deferred_native_encoder_raises() -> None:
    encoder = DeferredNativeAudioEncoder()
    with pytest.raises(DeferredNativeCodecError):
        encoder.encode(b"\x00" * UPLINK_PCM_FORMAT.expected_bytes)


def test_deferred_native_resampler_passthrough() -> None:
    resampler = DeferredNativeAudioResampler()
    pcm = b"\x00" * DOWNLINK_PCM_FORMAT.expected_bytes
    # Same format passthrough is allowed without native deps.
    assert resampler.resample(pcm, DOWNLINK_PCM_FORMAT, DOWNLINK_PCM_FORMAT) == pcm


def test_deferred_native_resampler_validates_buffer() -> None:
    resampler = DeferredNativeAudioResampler()
    with pytest.raises(InvalidPCMFormatError):
        resampler.resample(b"\x00" * 10, UPLINK_PCM_FORMAT, DOWNLINK_PCM_FORMAT)


def test_deferred_native_resampler_cross_rate_raises() -> None:
    resampler = DeferredNativeAudioResampler()
    pcm = b"\x00" * UPLINK_PCM_FORMAT.expected_bytes
    with pytest.raises(DeferredNativeResamplerError):
        resampler.resample(pcm, UPLINK_PCM_FORMAT, DOWNLINK_PCM_FORMAT)


@pytest.mark.skipif(not is_native_opus_available(), reason="libopus is unavailable")
@pytest.mark.parametrize("pcm_format", [UPLINK_PCM_FORMAT, DOWNLINK_PCM_FORMAT])
def test_native_opus_roundtrip_silence(pcm_format: PCMFormat) -> None:
    pcm = b"\x00" * pcm_format.expected_bytes
    with NativeOpusEncoder(pcm_format) as encoder, NativeOpusDecoder(
        pcm_format
    ) as decoder:
        packet = encoder.encode(pcm)
        decoded = decoder.decode(packet)

    assert packet
    assert len(decoded) == pcm_format.expected_bytes
    samples = array("h", decoded)
    if sys.byteorder != "little":
        samples.byteswap()
    assert max(abs(sample) for sample in samples) <= 2
    assert encoder.is_closed
    assert decoder.is_closed


@pytest.mark.skipif(not is_native_opus_available(), reason="libopus is unavailable")
def test_native_opus_rejects_invalid_input_and_double_close() -> None:
    encoder = NativeOpusEncoder()
    decoder = NativeOpusDecoder()

    with pytest.raises(InvalidPCMFormatError):
        encoder.encode(b"\x00")
    with pytest.raises(CodecError):
        decoder.decode(b"")
    with pytest.raises(CodecError):
        decoder.decode(b"not-an-opus-packet")
    with pytest.raises(CodecError, match="maximum allowed size"):
        decoder.decode(b"x" * (MAX_OPUS_PAYLOAD_BYTES + 1))

    encoder.close()
    encoder.close()
    decoder.close()
    decoder.close()
    with pytest.raises(CodecError):
        encoder.encode(b"\x00" * DOWNLINK_PCM_FORMAT.expected_bytes)
    with pytest.raises(CodecError):
        decoder.decode(b"packet")


def test_codec_factory_defaults_to_fake() -> None:
    settings = Settings(environment="test")

    assert isinstance(build_opus_decoder(settings), FakeOpusDecoder)
    assert isinstance(build_opus_encoder(settings), FakeOpusEncoder)


@pytest.mark.skipif(not is_native_opus_available(), reason="libopus is unavailable")
def test_codec_factory_builds_fresh_native_instances() -> None:
    settings = Settings(environment="test", audio_codec="native")

    first = build_opus_decoder(settings)
    second = build_opus_decoder(settings)
    encoder = build_opus_encoder(settings)
    try:
        assert isinstance(first, NativeOpusDecoder)
        assert isinstance(second, NativeOpusDecoder)
        assert isinstance(encoder, NativeOpusEncoder)
        assert first is not second
    finally:
        first.close()  # type: ignore[attr-defined]
        second.close()  # type: ignore[attr-defined]
        encoder.close()  # type: ignore[attr-defined]
