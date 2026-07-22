from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from statistics import median
from time import monotonic, perf_counter

import numpy as np
import soundfile as sf

from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider

SERVER_ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def operation_context(name: str, seconds: float = 60) -> OperationContext:
    return OperationContext("benchmark", name, 1, CancellationToken(), monotonic() + seconds)


async def benchmark(
    text: str,
    voice: str,
    *,
    asr_threads: int,
    tts_threads: int,
    apply_watermark: bool,
    runs: int,
    seed: int,
) -> dict[str, object]:
    asr_path = SERVER_ROOT / "models/sherpa-onnx-zipformer-vi-30m-int8"
    tts_path = SERVER_ROOT / "models/vieneu-v3-turbo"
    asr = SherpaZipformerAsrProvider(asr_path, num_threads=asr_threads)
    tts = VieNeuTtsProvider(
        tts_path,
        voice=voice,
        num_threads=tts_threads,
        apply_watermark=apply_watermark,
    )

    started = perf_counter()
    await asr.prewarm()
    asr_load_ms = (perf_counter() - started) * 1000
    wav_path = asr_path / "test_wavs/2.wav"
    audio, sample_rate = sf.read(wav_path, dtype="int16", always_2d=True)
    pcm = audio[:, 0].astype("<i2").tobytes()
    asr_runs_ms: list[float] = []
    transcript = None
    for index in range(runs):
        started = perf_counter()
        transcript = await asr.transcribe_pcm(
            pcm,
            sample_rate=sample_rate,
            locale="vi-VN",
            context=operation_context(f"asr:{index}"),
        )
        asr_runs_ms.append((perf_counter() - started) * 1000)
    assert transcript is not None

    started = perf_counter()
    await tts.prewarm()
    tts_load_ms = (perf_counter() - started) * 1000
    first_audio_runs_ms: list[float] = []
    synthesis_runs_ms: list[float] = []
    audio_seconds_runs: list[float] = []
    rtf_runs: list[float] = []
    for index in range(runs):
        np.random.seed(seed + index)
        started = perf_counter()
        first_audio_ms: float | None = None
        output_bytes = 0
        async for chunk in tts.synthesize(
            text, "vi-VN", operation_context(f"tts:{index}")
        ):
            if first_audio_ms is None and chunk.data:
                first_audio_ms = (perf_counter() - started) * 1000
            output_bytes += len(chunk.data)
        synthesis_ms = (perf_counter() - started) * 1000
        audio_seconds = output_bytes / (24_000 * 2)
        first_audio_runs_ms.append(first_audio_ms or 0)
        synthesis_runs_ms.append(synthesis_ms)
        audio_seconds_runs.append(audio_seconds)
        if audio_seconds:
            rtf_runs.append((synthesis_ms / 1000) / audio_seconds)
    return {
        "asr": {
            "load_ms": round(asr_load_ms, 2),
            "decode_ms": round(median(asr_runs_ms), 2),
            "decode_p95_ms": round(percentile(asr_runs_ms, 0.95), 2),
            "decode_max_ms": round(max(asr_runs_ms), 2),
            "audio_seconds": round(len(pcm) / (sample_rate * 2), 3),
            "text": transcript.text,
            "device": "cpu",
            "threads": asr_threads,
            "runs": runs,
        },
        "tts": {
            "load_ms": round(tts_load_ms, 2),
            "first_audio_ms": round(median(first_audio_runs_ms), 2),
            "first_audio_p95_ms": round(percentile(first_audio_runs_ms, 0.95), 2),
            "first_audio_max_ms": round(max(first_audio_runs_ms), 2),
            "synthesis_ms": round(median(synthesis_runs_ms), 2),
            "synthesis_p95_ms": round(percentile(synthesis_runs_ms, 0.95), 2),
            "synthesis_max_ms": round(max(synthesis_runs_ms), 2),
            "audio_seconds": round(median(audio_seconds_runs), 3),
            "rtf": round(median(rtf_runs), 3) if rtf_runs else None,
            "rtf_p95": round(percentile(rtf_runs, 0.95), 3) if rtf_runs else None,
            "device": "cpu",
            "voice": voice,
            "threads": tts_threads,
            "watermark": apply_watermark,
            "runs": runs,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="Xin chào, tôi là Veetee. Tôi có thể giúp gì cho bạn?")
    parser.add_argument("--voice", default="Ngọc Linh")
    parser.add_argument("--asr-threads", type=int, default=2, choices=range(1, 9))
    parser.add_argument("--tts-threads", type=int, default=6, choices=range(1, 9))
    parser.add_argument("--watermark", action="store_true")
    parser.add_argument("--runs", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--seed", type=int, default=20_260_722)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                benchmark(
                    args.text,
                    args.voice,
                    asr_threads=args.asr_threads,
                    tts_threads=args.tts_threads,
                    apply_watermark=args.watermark,
                    runs=args.runs,
                    seed=args.seed,
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
