"""Native libopus codec implementation using Python stdlib ctypes.

Implements stateful NativeOpusDecoder and NativeOpusEncoder for strict Veetee
uplink (16kHz mono 60ms) and downlink (24kHz mono 60ms) PCM audio contracts.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
from typing import Any, Self, cast

from veetee_server.audio.codec import (
    DOWNLINK_PCM_FORMAT,
    UPLINK_PCM_FORMAT,
    CodecError,
    InvalidPCMFormatError,
    PCMFormat,
)

logger = logging.getLogger(__name__)

# Opus error codes
OPUS_OK = 0
OPUS_BAD_ARG = -1
OPUS_BUFFER_TOO_SMALL = -2
OPUS_INTERNAL_ERROR = -3
OPUS_INVALID_PACKET = -4
OPUS_UNIMPLEMENTED = -5
OPUS_INVALID_STATE = -6
OPUS_ALLOC_FAIL = -7

# Opus application modes
OPUS_APPLICATION_VOIP = 2048
OPUS_APPLICATION_AUDIO = 2049
OPUS_APPLICATION_RESTRICTED_LOWDELAY = 2051

# RFC 6716 caps one Opus frame at 1275 bytes. A 60 ms packet can contain
# three 20 ms frames, so the negotiated Veetee packet bound is three frames.
MAX_OPUS_PAYLOAD_BYTES = 3 * 1275


class _LibOpusHolder:
    """Manages loading and binding of libopus C library functions."""

    _lib: ctypes.CDLL | None = None
    _load_error: str | None = None

    @classmethod
    def get_library(cls) -> ctypes.CDLL:
        if cls._lib is not None:
            return cls._lib
        if cls._load_error is not None:
            raise CodecError(f"Native libopus is unavailable: {cls._load_error}")

        candidates: list[str] = ["libopus.so.0", "libopus.so"]
        discovered = ctypes.util.find_library("opus")
        if discovered and discovered not in candidates:
            candidates.insert(0, discovered)

        last_err: Exception | None = None
        for name in candidates:
            try:
                lib = ctypes.CDLL(name)
                lib.opus_strerror.restype = ctypes.c_char_p
                lib.opus_strerror.argtypes = [ctypes.c_int]

                lib.opus_encoder_create.restype = ctypes.c_void_p
                lib.opus_encoder_create.argtypes = [
                    ctypes.c_int32,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                ]

                lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]

                lib.opus_encode.restype = ctypes.c_int32
                lib.opus_encode.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int16),
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_ubyte),
                    ctypes.c_int32,
                ]

                lib.opus_decoder_create.restype = ctypes.c_void_p
                lib.opus_decoder_create.argtypes = [
                    ctypes.c_int32,
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_int),
                ]

                lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

                lib.opus_decode.restype = ctypes.c_int
                lib.opus_decode.argtypes = [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_ubyte),
                    ctypes.c_int32,
                    ctypes.POINTER(ctypes.c_int16),
                    ctypes.c_int,
                    ctypes.c_int,
                ]

                cls._lib = lib
                return lib
            except Exception as exc:
                last_err = exc

        cls._load_error = str(last_err) if last_err else "libopus shared library not found"
        raise CodecError(f"Native libopus is unavailable: {cls._load_error}")

    @classmethod
    def is_available(cls) -> bool:
        try:
            cls.get_library()
            return True
        except Exception:
            return False

    @classmethod
    def opus_strerror(cls, error_code: int) -> str:
        try:
            lib = cls.get_library()
            res = lib.opus_strerror(error_code)
            if res:
                return cast(bytes, res).decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"Unknown error ({error_code})"


def is_native_opus_available() -> bool:
    """Checks if native libopus C library is loadable and ready."""
    return _LibOpusHolder.is_available()


class NativeOpusDecoder:
    """Stateful native Opus decoder wrapping C libopus.

    Supported default contract: uplink 16kHz mono 60ms s16le PCM.
    """

    def __init__(self, pcm_format: PCMFormat = UPLINK_PCM_FORMAT) -> None:
        self.pcm_format = pcm_format
        self._decoder_ptr: ctypes.c_void_p | None = None
        if pcm_format.sample_width_bytes != 2 or pcm_format.endianness != "little":
            raise InvalidPCMFormatError(
                f"Unsupported PCM format for Opus decoder: {pcm_format}"
            )
        lib = _LibOpusHolder.get_library()
        err = ctypes.c_int(0)
        ptr = lib.opus_decoder_create(
            pcm_format.sample_rate, pcm_format.channels, ctypes.byref(err)
        )
        if err.value != OPUS_OK or not ptr:
            err_msg = _LibOpusHolder.opus_strerror(err.value)
            raise CodecError(f"Failed to create native Opus decoder: {err_msg}")
        self._decoder_ptr = ptr

    @property
    def is_closed(self) -> bool:
        return self._decoder_ptr is None

    def decode(self, payload: bytes) -> bytes:
        if self._decoder_ptr is None:
            raise CodecError("Decoder is closed")
        if not payload:
            raise CodecError("Cannot decode empty Opus payload")
        if len(payload) > MAX_OPUS_PAYLOAD_BYTES:
            raise CodecError(
                f"Opus payload exceeds maximum allowed size ({len(payload)} bytes)"
            )

        lib = _LibOpusHolder.get_library()
        in_buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        out_pcm = (ctypes.c_int16 * self.pcm_format.expected_samples)()

        ret = lib.opus_decode(
            self._decoder_ptr,
            in_buf,
            len(payload),
            out_pcm,
            self.pcm_format.expected_samples,
            0,
        )
        if ret < 0:
            err_msg = _LibOpusHolder.opus_strerror(ret)
            raise CodecError(f"Opus decoding failed with code {ret}: {err_msg}")

        decoded_pcm = bytes(out_pcm)[: ret * 2]
        if (
            len(decoded_pcm) != self.pcm_format.expected_bytes
            or ret != self.pcm_format.expected_samples
        ):
            raise InvalidPCMFormatError(
                "Decoded PCM buffer length mismatch: expected "
                f"{self.pcm_format.expected_bytes} bytes "
                f"({self.pcm_format.expected_samples} samples), got "
                f"{len(decoded_pcm)} bytes ({ret} samples)"
            )
        return decoded_pcm

    def close(self) -> None:
        ptr = self._decoder_ptr
        if ptr is not None:
            self._decoder_ptr = None
            try:
                lib = _LibOpusHolder.get_library()
                lib.opus_decoder_destroy(ptr)
            except Exception as exc:
                logger.warning("Error destroying Opus decoder: %s", exc)

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class NativeOpusEncoder:
    """Stateful native Opus encoder wrapping C libopus.

    Supported default contract: downlink 24kHz mono 60ms s16le PCM.
    """

    def __init__(
        self,
        pcm_format: PCMFormat = DOWNLINK_PCM_FORMAT,
        application: int = OPUS_APPLICATION_VOIP,
    ) -> None:
        self.pcm_format = pcm_format
        self._encoder_ptr: ctypes.c_void_p | None = None
        if pcm_format.sample_width_bytes != 2 or pcm_format.endianness != "little":
            raise InvalidPCMFormatError(
                f"Unsupported PCM format for Opus encoder: {pcm_format}"
            )
        lib = _LibOpusHolder.get_library()
        err = ctypes.c_int(0)
        ptr = lib.opus_encoder_create(
            pcm_format.sample_rate, pcm_format.channels, application, ctypes.byref(err)
        )
        if err.value != OPUS_OK or not ptr:
            err_msg = _LibOpusHolder.opus_strerror(err.value)
            raise CodecError(f"Failed to create native Opus encoder: {err_msg}")
        self._encoder_ptr = ptr

    @property
    def is_closed(self) -> bool:
        return self._encoder_ptr is None

    def encode(self, pcm_data: bytes) -> bytes:
        if self._encoder_ptr is None:
            raise CodecError("Encoder is closed")
        self.pcm_format.validate_buffer(pcm_data)

        lib = _LibOpusHolder.get_library()
        pcm_buf = (ctypes.c_int16 * self.pcm_format.expected_samples).from_buffer_copy(
            pcm_data
        )
        out_buf = (ctypes.c_ubyte * MAX_OPUS_PAYLOAD_BYTES)()

        ret = lib.opus_encode(
            self._encoder_ptr,
            pcm_buf,
            self.pcm_format.expected_samples,
            out_buf,
            MAX_OPUS_PAYLOAD_BYTES,
        )
        if ret < 0:
            err_msg = _LibOpusHolder.opus_strerror(ret)
            raise CodecError(f"Opus encoding failed with code {ret}: {err_msg}")

        return bytes(out_buf[:ret])

    def close(self) -> None:
        ptr = self._encoder_ptr
        if ptr is not None:
            self._encoder_ptr = None
            try:
                lib = _LibOpusHolder.get_library()
                lib.opus_encoder_destroy(ptr)
            except Exception as exc:
                logger.warning("Error destroying Opus encoder: %s", exc)

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
