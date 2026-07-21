from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
import soundfile as sf
import soxr  # type: ignore[import-untyped]
import websockets

from veetee_voice_server.transport.opus import OpusEncoder

SERVER_ROOT = Path(__file__).resolve().parents[1]


def load_pcm(path: Path, sample_rate: int) -> bytes:
    audio, source_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio[:, 0]
    if source_rate != sample_rate:
        mono = soxr.resample(mono, source_rate, sample_rate, quality="HQ")
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2")
    trailing_silence = np.zeros(sample_rate, dtype="<i2")
    return pcm.tobytes() + trailing_silence.tobytes()


async def run(
    url: str, wav_path: Path, timeout: float, *, abort_on_first_audio: bool
) -> dict[str, Any]:
    frame_samples = 960
    frame_bytes = frame_samples * 2
    pcm = load_pcm(wav_path, 16_000)
    if len(pcm) % frame_bytes:
        pcm += b"\0" * (frame_bytes - len(pcm) % frame_bytes)

    result: dict[str, Any] = {
        "wav": wav_path.name,
        "uplink_frames": len(pcm) // frame_bytes,
        "downlink_frames": 0,
        "event_counts": {},
        "lifecycle": [],
    }
    timings: dict[str, float] = {}
    async with websockets.connect(url, max_size=16 * 1024) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "hello",
                    "version": 1,
                    "features": {"mcp": True, "aec": False, "glyph_push": False},
                    "transport": "websocket",
                    "audio_params": {
                        "format": "opus",
                        "sample_rate": 16_000,
                        "channels": 1,
                        "frame_duration": 60,
                    },
                },
                separators=(",", ":"),
            )
        )
        hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
        session_id = hello["session_id"]
        await websocket.send(
            json.dumps(
                {
                    "session_id": session_id,
                    "type": "listen",
                    "state": "start",
                    "mode": "auto",
                    "source": "button",
                },
                separators=(",", ":"),
            )
        )

        encoder = OpusEncoder(16_000)
        try:
            for offset in range(0, len(pcm), frame_bytes):
                packet = encoder.encode(
                    pcm[offset : offset + frame_bytes], frame_samples=frame_samples
                )
                await websocket.send(packet)
                await asyncio.sleep(0.06)
        finally:
            encoder.close()

        started = perf_counter()
        saw_stt = False
        saw_thinking = False
        saw_tts_start = False
        saw_tts_stop = False
        abort_sent = False
        listening_after_abort = False
        frames_after_abort = 0
        while not (
            (saw_tts_stop and not abort_on_first_audio)
            or (saw_tts_stop and listening_after_abort and abort_on_first_audio)
        ):
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except TimeoutError:
                result["timeout"] = True
                break
            elapsed_ms = (perf_counter() - started) * 1000
            if isinstance(message, bytes):
                result["downlink_frames"] += 1
                timings.setdefault("first_audio_ms", elapsed_ms)
                if abort_sent:
                    frames_after_abort += 1
                elif abort_on_first_audio:
                    await websocket.send(
                        json.dumps(
                            {
                                "session_id": session_id,
                                "type": "abort",
                                "reason": "e2e_interrupt",
                                "source": "button",
                            },
                            separators=(",", ":"),
                        )
                    )
                    abort_sent = True
                    timings["abort_sent_ms"] = elapsed_ms
                continue
            event = json.loads(message)
            event_type = event.get("type")
            state = event.get("state")
            event_key = f"{event_type}:{state}" if state else str(event_type)
            event_counts = result["event_counts"]
            assert isinstance(event_counts, dict)
            event_counts[event_key] = event_counts.get(event_key, 0) + 1
            if event_type == "stt":
                saw_stt = True
                result["transcript"] = event.get("text", "")
                timings["stt_ms"] = elapsed_ms
                result["lifecycle"].append("stt")
            elif event_type == "llm" and event.get("emotion") == "thinking":
                saw_thinking = True
                timings["thinking_ms"] = elapsed_ms
                result["lifecycle"].append("llm:thinking")
            elif event_type == "llm" and isinstance(event.get("text"), str):
                result["llm_characters"] = result.get("llm_characters", 0) + len(
                    event["text"]
                )
            elif event_type == "tts" and state == "start":
                saw_tts_start = True
                timings["tts_start_ms"] = elapsed_ms
                result["lifecycle"].append("tts:start")
            elif event_type == "tts" and state == "stop":
                saw_tts_stop = True
                timings["tts_stop_ms"] = elapsed_ms
                result["lifecycle"].append("tts:stop")
            elif event_type == "listen" and state == "start" and saw_stt:
                if abort_sent:
                    listening_after_abort = True
                    timings["listening_after_abort_ms"] = elapsed_ms
                    result["lifecycle"].append("listen:start")
                elif not saw_tts_start:
                    result["ended_without_tts"] = True
                    break

    result["timings"] = {key: round(value, 2) for key, value in timings.items()}
    if abort_on_first_audio:
        result["frames_after_abort"] = frames_after_abort
        result["ok"] = all(
            (
                saw_stt,
                saw_thinking,
                saw_tts_start,
                saw_tts_stop,
                abort_sent,
                listening_after_abort,
                frames_after_abort <= 2,
            )
        )
    else:
        result["ok"] = all(
            (
                saw_stt,
                saw_thinking,
                saw_tts_start,
                saw_tts_stop,
                result["downlink_frames"] > 0,
            )
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/xiaozhi/v1/")
    parser.add_argument(
        "--wav",
        type=Path,
        default=SERVER_ROOT / "models/sherpa-onnx-zipformer-vi-30m-int8/test_wavs/2.wav",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--abort-on-first-audio", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.url,
            args.wav,
            args.timeout,
            abort_on_first_audio=args.abort_on_first_audio,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
