from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic, perf_counter

import soundfile as sf

from veetee_voice_server.conversation.cancellation import CancellationToken, OperationContext
from veetee_voice_server.providers.local_asr import SherpaZipformerAsrProvider
from veetee_voice_server.providers.local_tts import VieNeuTtsProvider

SERVER_ROOT = Path(__file__).resolve().parents[1]


def operation_context(name: str, seconds: float = 60) -> OperationContext:
    return OperationContext("benchmark", name, 1, CancellationToken(), monotonic() + seconds)


async def benchmark(text: str, voice: str) -> dict[str, object]:
    asr_path = SERVER_ROOT / "models/sherpa-onnx-zipformer-vi-30m-int8"
    tts_path = SERVER_ROOT / "models/vieneu-v3-turbo"
    asr = SherpaZipformerAsrProvider(asr_path, num_threads=4)
    tts = VieNeuTtsProvider(tts_path, voice=voice, num_threads=4, apply_watermark=False)

    started = perf_counter()
    await asr.prewarm()
    asr_load_ms = (perf_counter() - started) * 1000
    wav_path = asr_path / "test_wavs/2.wav"
    audio, sample_rate = sf.read(wav_path, dtype="int16", always_2d=True)
    pcm = audio[:, 0].astype("<i2").tobytes()
    started = perf_counter()
    transcript = await asr.transcribe_pcm(
        pcm,
        sample_rate=sample_rate,
        locale="vi-VN",
        context=operation_context("asr"),
    )
    asr_ms = (perf_counter() - started) * 1000

    started = perf_counter()
    await tts.prewarm()
    tts_load_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    first_audio_ms: float | None = None
    output_bytes = 0
    async for chunk in tts.synthesize(text, "vi-VN", operation_context("tts")):
        if first_audio_ms is None and chunk.data:
            first_audio_ms = (perf_counter() - started) * 1000
        output_bytes += len(chunk.data)
    tts_ms = (perf_counter() - started) * 1000
    audio_seconds = output_bytes / (24_000 * 2)
    return {
        "asr": {
            "load_ms": round(asr_load_ms, 2),
            "decode_ms": round(asr_ms, 2),
            "audio_seconds": round(len(pcm) / (sample_rate * 2), 3),
            "text": transcript.text,
        },
        "tts": {
            "load_ms": round(tts_load_ms, 2),
            "first_audio_ms": round(first_audio_ms or 0, 2),
            "synthesis_ms": round(tts_ms, 2),
            "audio_seconds": round(audio_seconds, 3),
            "rtf": round((tts_ms / 1000) / audio_seconds, 3) if audio_seconds else None,
            "voice": voice,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="Xin chào, tôi là Veetee. Tôi có thể giúp gì cho bạn?")
    parser.add_argument("--voice", default="Ngọc Linh")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(benchmark(args.text, args.voice)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
