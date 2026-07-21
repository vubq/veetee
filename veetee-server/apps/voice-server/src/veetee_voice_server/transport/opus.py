from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass, field


class OpusError(RuntimeError):
    pass


_OPUS_APPLICATION_AUDIO = 2049
_OPUS_OK = 0


def _load_opus() -> ctypes.CDLL:
    library = ctypes.util.find_library("opus") or "libopus.so.0"
    return ctypes.CDLL(library)


@dataclass(slots=True)
class OpusDecoder:
    sample_rate: int = 16_000
    channels: int = 1
    _lib: ctypes.CDLL = field(init=False, repr=False)
    _handle: ctypes.c_void_p | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._lib = _load_opus()
        self._lib.opus_decoder_create.argtypes = [
            ctypes.c_int32,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_decoder_create.restype = ctypes.c_void_p
        self._lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.opus_decode.restype = ctypes.c_int
        self._lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_decoder_destroy.restype = None
        error = ctypes.c_int()
        self._handle = self._lib.opus_decoder_create(
            self.sample_rate, self.channels, ctypes.byref(error)
        )
        if not self._handle or error.value != _OPUS_OK:
            raise OpusError(f"opus decoder init failed: {error.value}")

    def decode(self, packet: bytes, *, max_frame_samples: int | None = None) -> bytes:
        if not packet:
            return b""
        capacity = max_frame_samples or self.sample_rate // 25 * 3
        output = (ctypes.c_int16 * (capacity * self.channels))()
        encoded = ctypes.create_string_buffer(packet)
        count = self._lib.opus_decode(
            self._handle,
            ctypes.cast(encoded, ctypes.c_void_p),
            len(packet),
            output,
            capacity,
            0,
        )
        if count < 0:
            raise OpusError(f"opus decode failed: {count}")
        return ctypes.string_at(output, count * self.channels * ctypes.sizeof(ctypes.c_int16))

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._lib.opus_decoder_destroy(self._handle)
            self._handle = None


@dataclass(slots=True)
class OpusEncoder:
    sample_rate: int = 16_000
    channels: int = 1
    bitrate: int = 24_000
    _lib: ctypes.CDLL = field(init=False, repr=False)
    _handle: ctypes.c_void_p | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._lib = _load_opus()
        self._lib.opus_encoder_create.argtypes = [
            ctypes.c_int32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_encoder_create.restype = ctypes.c_void_p
        self._lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._lib.opus_encoder_ctl.restype = ctypes.c_int
        self._lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int32,
        ]
        self._lib.opus_encode.restype = ctypes.c_int
        self._lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_encoder_destroy.restype = None
        error = ctypes.c_int()
        self._handle = self._lib.opus_encoder_create(
            self.sample_rate, self.channels, _OPUS_APPLICATION_AUDIO, ctypes.byref(error)
        )
        if not self._handle or error.value != _OPUS_OK:
            raise OpusError(f"opus encoder init failed: {error.value}")
        if self.bitrate > 0:
            # OPUS_SET_BITRATE_REQUEST is 4002.
            self._lib.opus_encoder_ctl(self._handle, 4002, self.bitrate)

    def encode(self, pcm_s16le: bytes, *, frame_samples: int | None = None) -> bytes:
        if len(pcm_s16le) % (2 * self.channels):
            raise ValueError("PCM input is not aligned to channel sample width")
        samples = len(pcm_s16le) // (2 * self.channels)
        if frame_samples is not None and samples != frame_samples:
            raise ValueError(f"expected {frame_samples} samples, got {samples}")
        raw = (ctypes.c_int16 * (samples * self.channels)).from_buffer_copy(pcm_s16le)
        output = ctypes.create_string_buffer(4_000)
        count = self._lib.opus_encode(self._handle, raw, samples, output, len(output))
        if count < 0:
            raise OpusError(f"opus encode failed: {count}")
        return bytes(output.raw[:count])

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._lib.opus_encoder_destroy(self._handle)
            self._handle = None
