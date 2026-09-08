import opuslib_next
import numpy as np
import soxr
import logging
from typing import List, Generator

logger = logging.getLogger("AudioUtils")

class AudioCodec:
    SUPPORTED_OPUS_FRAME_DURATIONS_MS = {5, 10, 20, 40, 60}

    def __init__(
        self,
        in_sample_rate: int = 16000,
        out_sample_rate: int = 24000,
        frame_duration_ms: int = 60,
        in_frame_duration_ms: int = 60,
    ):
        self.in_sample_rate = in_sample_rate
        self.out_sample_rate = out_sample_rate
        self.frame_duration_ms = frame_duration_ms

        # 16kHz Opus decoder for microphone input
        self.decoder_16k = opuslib_next.Decoder(in_sample_rate, 1)
        self.in_frame_duration_ms = 60
        self.in_frame_size = int(in_sample_rate * 60 / 1000)
        self.configure_input_frame_duration(in_frame_duration_ms)
        
        # 24kHz Opus encoder for speaker output
        self.encoder_24k = opuslib_next.Encoder(out_sample_rate, 1, opuslib_next.APPLICATION_VOIP)
        self.out_frame_size = int(out_sample_rate * frame_duration_ms / 1000) # 1440 samples @ 24kHz 60ms

    def configure_input_frame_duration(self, frame_duration_ms: int) -> bool:
        """Apply a client-reported Opus frame duration when it is supported."""
        try:
            duration = int(frame_duration_ms)
        except (TypeError, ValueError):
            return False
        if duration not in self.SUPPORTED_OPUS_FRAME_DURATIONS_MS:
            return False
        self.in_frame_duration_ms = duration
        self.in_frame_size = int(self.in_sample_rate * duration / 1000)
        return True

    def decode_opus_to_pcm16(self, opus_bytes: bytes) -> bytes:
        """
        Decodes incoming 16kHz Opus packet to 16-bit PCM mono bytes.
        """
        try:
            pcm = self.decoder_16k.decode(opus_bytes, self.in_frame_size)
            return pcm
        except Exception as e:
            logger.debug(f"Opus decode error: {e}")
            return b""

    def encode_pcm24_to_opus(self, pcm_bytes: bytes) -> bytes:
        """
        Encodes 24kHz 16-bit PCM mono bytes (1440 samples = 2880 bytes) to Opus.
        """
        try:
            return self.encoder_24k.encode(pcm_bytes, self.out_frame_size)
        except Exception as e:
            logger.debug(f"Opus encode error: {e}")
            return b""

    def resample_float32_48k_to_pcm16_24k(self, audio_48k: np.ndarray) -> np.ndarray:
        """
        Resamples 48kHz float32 audio [-1.0, 1.0] to 24kHz int16.
        """
        if len(audio_48k) == 0:
            return np.array([], dtype=np.int16)
        
        # Resample 48000 -> 24000
        audio_24k_f = soxr.resample(audio_48k, 48000, self.out_sample_rate)
        # Clip and convert to int16
        audio_24k_clipped = np.clip(audio_24k_f, -1.0, 1.0)
        audio_24k_int16 = (audio_24k_clipped * 32767.0).astype(np.int16)
        return audio_24k_int16

    def chunk_pcm_to_opus_frames(self, pcm_int16: np.ndarray, remainder_buffer: bytearray) -> List[bytes]:
        """
        Appends pcm_int16 to remainder_buffer, chops into exact 60ms frames, and encodes to Opus packets.
        """
        frame_bytes_needed = self.out_frame_size * 2 # 2880 bytes for 1440 samples
        remainder_buffer.extend(pcm_int16.tobytes())
        
        opus_frames = []
        while len(remainder_buffer) >= frame_bytes_needed:
            frame_raw = bytes(remainder_buffer[:frame_bytes_needed])
            del remainder_buffer[:frame_bytes_needed]
            opus_packet = self.encode_pcm24_to_opus(frame_raw)
            if opus_packet:
                opus_frames.append(opus_packet)
        return opus_frames

    def flush_remainder_to_opus_frame(self, remainder_buffer: bytearray) -> List[bytes]:
        """
        Flushes any remaining audio bytes by zero-padding to a full frame.
        """
        frame_bytes_needed = self.out_frame_size * 2
        opus_frames = []
        if len(remainder_buffer) > 0:
            pad_size = frame_bytes_needed - len(remainder_buffer)
            remainder_buffer.extend(b"\x00" * pad_size)
            frame_raw = bytes(remainder_buffer[:frame_bytes_needed])
            remainder_buffer.clear()
            opus_packet = self.encode_pcm24_to_opus(frame_raw)
            if opus_packet:
                opus_frames.append(opus_packet)
        return opus_frames
