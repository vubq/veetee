#!/usr/bin/env python3
"""Benchmark PhoWhisper CTranslate2 and Silero ONNX using local harmless audio."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np


MODELS = {
    "small": "mad1999/pho-whisper-small-ct2",
    "medium": "mad1999/pho-whisper-medium-ct2",
}


def load_audio(path: Path, sample_rate: int = 16000) -> np.ndarray:
    import librosa

    audio, _ = librosa.load(path.as_posix(), sr=sample_rate, mono=True)
    return audio.astype(np.float32)


def transcribe(model_name: str, audio: np.ndarray, device: str, compute_type: str) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    model = WhisperModel(MODELS[model_name], device=device, compute_type=compute_type)
    loaded_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    segments, info = model.transcribe(
        audio,
        language="vi",
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    infer_ms = (time.perf_counter() - started) * 1000
    return {
        "model": model_name,
        "loaded_ms": round(loaded_ms, 1),
        "infer_ms": round(infer_ms, 1),
        "audio_duration_s": round(len(audio) / 16000, 3),
        "realtime_factor": round(infer_ms / 1000 / (len(audio) / 16000), 3),
        "language": getattr(info, "language", None),
        "text_chars": len(text),
        "text_recorded": False,
    }


def concurrency_probe(model_name: str, audio: np.ndarray, device: str, compute_type: str) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    model = WhisperModel(MODELS[model_name], device=device, compute_type=compute_type)
    lock = threading.Lock()

    def run_one() -> float:
        started = time.perf_counter()
        with lock:
            segments, _ = model.transcribe(audio, language="vi", beam_size=1, vad_filter=False)
            list(segments)
        return (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        durations = list(executor.map(lambda _: run_one(), range(2)))
    return {
        "model": model_name,
        "workers": 2,
        "serialized_durations_ms": [round(value, 1) for value in durations],
        "wall_ms": round((time.perf_counter() - started) * 1000, 1),
        "note": "shared model probe serializes GPU inference to avoid unsafe concurrent decode",
    }


def silero_probe(audio: np.ndarray) -> list[dict[str, Any]]:
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad(onnx=True)
    results: list[dict[str, Any]] = []
    for threshold in (0.35, 0.5, 0.65):
        started = time.perf_counter()
        timestamps = get_speech_timestamps(
            audio,
            model,
            sampling_rate=16000,
            threshold=threshold,
            min_speech_duration_ms=250,
            min_silence_duration_ms=150,
            speech_pad_ms=80,
        )
        results.append(
            {
                "threshold": threshold,
                "segments": len(timestamps),
                "speech_ms": sum(item["end"] - item["start"] for item in timestamps) / 16,
                "infer_ms": round((time.perf_counter() - started) * 1000, 1),
                "frame_sample_rate": 16000,
                "pre_roll_ms": 80,
                "silence_ms": 150,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=Path("/home/quangvu/test-vne.wav"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--skip-medium", action="store_true")
    args = parser.parse_args()
    audio = load_audio(args.audio)
    models = ("small",) if args.skip_medium else tuple(MODELS)
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "audio_source": args.audio.as_posix(),
        "audio_duration_s": round(len(audio) / 16000, 3),
        "audio_ground_truth": False,
        "models": [],
        "silero": silero_probe(audio),
    }
    for model_name in models:
        result = transcribe(model_name, audio, args.device, args.compute_type)
        result["concurrency"] = concurrency_probe(model_name, audio, args.device, args.compute_type)
        report["models"].append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
