"""Tests for Veetee audio PCM formats, fake Opus codec and deferred native boundaries (M1.5)."""

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
    PCMFormat,
)


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
