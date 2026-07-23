from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from veetee_voice_server.conversation.types import (
    InputEvidence,
    InputSource,
    WakeSource,
)

_MIN_DBFS = -120.0
_CLIPPED_SAMPLE_ABSOLUTE = 32_760


def build_input_evidence(
    pcm_s16le: bytes,
    *,
    sample_rate: int,
    source: InputSource,
    wake_source: WakeSource | None,
    vad_probabilities: Sequence[float] = (),
    noise_pcm_s16le: bytes = b"",
    server_buffer_truncated: bool = False,
    aec_enabled: bool = False,
) -> InputEvidence:
    if not pcm_s16le or len(pcm_s16le) % 2:
        raise ValueError("Input evidence requires non-empty PCM16 audio")
    if sample_rate <= 0:
        raise ValueError("Input evidence requires a positive sample rate")

    samples = np.frombuffer(pcm_s16le, dtype="<i2")
    signal_rms = _rms_dbfs(samples)
    noise_rms = (
        _rms_dbfs(np.frombuffer(noise_pcm_s16le, dtype="<i2"))
        if noise_pcm_s16le and len(noise_pcm_s16le) % 2 == 0
        else None
    )
    probabilities = [
        min(max(float(probability), 0.0), 1.0)
        for probability in vad_probabilities
        if math.isfinite(float(probability))
    ]
    peak = int(np.max(np.abs(samples.astype(np.int32))))
    speech_frames = sum(probability >= 0.5 for probability in probabilities)

    return InputEvidence(
        source=source,
        wake_source=wake_source,
        utterance_duration_ms=round(samples.size * 1_000 / sample_rate),
        vad_mean_probability=(
            round(sum(probabilities) / len(probabilities), 4) if probabilities else None
        ),
        vad_peak_probability=round(max(probabilities), 4) if probabilities else None,
        vad_speech_ratio=(
            round(speech_frames / len(probabilities), 4) if probabilities else None
        ),
        signal_rms_dbfs=round(signal_rms, 2),
        signal_peak_dbfs=round(_amplitude_dbfs(peak), 2),
        noise_rms_dbfs=round(noise_rms, 2) if noise_rms is not None else None,
        estimated_snr_db=(
            round(min(max(signal_rms - noise_rms, -20.0), 80.0), 2)
            if noise_rms is not None
            else None
        ),
        clipping_ratio=round(
            float(
                np.count_nonzero(
                    np.abs(samples.astype(np.int32)) >= _CLIPPED_SAMPLE_ABSOLUTE
                )
            )
            / samples.size,
            6,
        ),
        server_buffer_truncated=server_buffer_truncated,
        # Raw WebSocket V1 has no packet sequence or far-end reference.
        packet_loss_ratio=None,
        audio_overrun=None,
        aec_enabled=aec_enabled,
        self_echo_probability=None,
        target_speaker_probability=None,
    )


def input_evidence_payload(evidence: InputEvidence | None) -> dict[str, object]:
    if evidence is None:
        return {
            "source": "unknown",
            "wake_source": None,
            "signal": None,
            "integrity": {
                "server_buffer_truncated": False,
                "packet_loss_ratio": None,
                "audio_overrun": None,
            },
            "aec": {"enabled": False, "self_echo_probability": None},
            "speaker": {"target_probability": None},
        }
    return {
        "source": evidence.source.value,
        "wake_source": (
            evidence.wake_source.value if evidence.wake_source is not None else None
        ),
        "signal": {
            "utterance_duration_ms": evidence.utterance_duration_ms,
            "vad_mean_probability": evidence.vad_mean_probability,
            "vad_peak_probability": evidence.vad_peak_probability,
            "vad_speech_ratio": evidence.vad_speech_ratio,
            "signal_rms_dbfs": evidence.signal_rms_dbfs,
            "signal_peak_dbfs": evidence.signal_peak_dbfs,
            "noise_rms_dbfs": evidence.noise_rms_dbfs,
            "estimated_snr_db": evidence.estimated_snr_db,
            "clipping_ratio": evidence.clipping_ratio,
        },
        "integrity": {
            "server_buffer_truncated": evidence.server_buffer_truncated,
            "packet_loss_ratio": evidence.packet_loss_ratio,
            "audio_overrun": evidence.audio_overrun,
        },
        "aec": {
            "enabled": evidence.aec_enabled,
            "self_echo_probability": evidence.self_echo_probability,
        },
        "speaker": {"target_probability": evidence.target_speaker_probability},
    }


def _rms_dbfs(samples: np.ndarray[Any, Any]) -> float:
    if samples.size == 0:
        return _MIN_DBFS
    normalized = samples.astype(np.float64) / 32_768.0
    rms = float(np.sqrt(np.mean(np.square(normalized))))
    return _amplitude_dbfs(rms)


def _amplitude_dbfs(amplitude: float | int) -> float:
    normalized = (
        float(amplitude) / 32_768.0 if isinstance(amplitude, int) else float(amplitude)
    )
    if normalized <= 0:
        return _MIN_DBFS
    return max(20.0 * math.log10(normalized), _MIN_DBFS)
