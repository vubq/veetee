#!/usr/bin/env python3
"""Reproducible stock-protocol voice-pipeline benchmark.

The benchmark consumes a pre-generated WAV fixture. It deliberately does not
instantiate the server TTS model, so measurement cannot contend with VieNeu on
the GPU. `speech_end_sample` is a label in the fixture, not ASR-final time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


FRAME_MS = 60
INPUT_RATE = 16000
FRAME_SAMPLES = INPUT_RATE * FRAME_MS // 1000


class BenchmarkError(RuntimeError):
    pass


@dataclass
class Fixture:
    path: str
    pcm16: bytes
    speech_end_sample: int
    source_sample_rate: int


@dataclass
class RunTrace:
    run_index: int
    measured: bool
    mode: str
    session_id: str = ""
    transcript: str = ""
    marks: Dict[str, float] = field(default_factory=dict)
    received_audio_frames: int = 0
    outcome: str = "running"
    error: str = ""

    def mark(self, name: str, at: Optional[float] = None) -> None:
        self.marks.setdefault(name, time.perf_counter() if at is None else at)

    def delta_ms(self, start: str, end: str) -> Optional[float]:
        if start not in self.marks or end not in self.marks:
            return None
        return round((self.marks[end] - self.marks[start]) * 1000.0, 3)

    def record(self) -> Dict[str, Any]:
        return {
            "run_index": self.run_index,
            "measured": self.measured,
            "mode": self.mode,
            "session_id": self.session_id,
            "outcome": self.outcome,
            "error": self.error or None,
            "transcript": self.transcript,
            "received_audio_frames": self.received_audio_frames,
            "speech_end_to_stt_final_ms": self.delta_ms("speech_end", "stt_final_received"),
            "speech_end_to_first_clause_ms": self.delta_ms("speech_end", "first_clause_received"),
            "speech_end_to_first_audio_received_ms": self.delta_ms("speech_end", "first_binary_received"),
            "first_audio_to_tts_stop_ms": self.delta_ms("first_binary_received", "tts_stop_received"),
            "listen_stop_to_first_audio_received_ms": self.delta_ms("listen_stop_sent", "first_binary_received"),
            "marks": self.marks,
            "observability": {
                "llm_request_count": None,
                "provider_attempt_count": None,
                "token_usage": None,
                "tts_rtf": None,
                "server_queue_wait_ms": None,
            },
        }


def _load_metadata(path: Path) -> Dict[str, Any]:
    metadata_path = path.with_suffix(path.suffix + ".json")
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def load_fixture(path: Path, speech_end_sample: Optional[int]) -> Fixture:
    try:
        import numpy as np
        import soxr
    except Exception as exc:
        raise BenchmarkError(f"audio dependencies unavailable: {exc}") from exc

    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        source_rate = wav_file.getframerate()
        source_frames = wav_file.getnframes()
        raw = wav_file.readframes(source_frames)

    if channels != 1 or sample_width != 2:
        raise BenchmarkError("fixture WAV must be mono PCM16")
    metadata = _load_metadata(path)
    if speech_end_sample is None:
        value = metadata.get("speech_end_sample")
        if value is None:
            raise BenchmarkError(
                "speech_end_sample is required via --speech-end-sample or <fixture>.wav.json"
            )
        speech_end_sample = int(value)
    if speech_end_sample < 1 or speech_end_sample > source_frames:
        raise BenchmarkError(
            f"speech_end_sample={speech_end_sample} outside fixture range 1..{source_frames}"
        )

    samples = np.frombuffer(raw, dtype=np.int16)
    end_16k = int(round(speech_end_sample * INPUT_RATE / source_rate))
    if source_rate != INPUT_RATE:
        samples_f = samples.astype(np.float32) / 32768.0
        samples_f = soxr.resample(samples_f, source_rate, INPUT_RATE)
        samples = (np.clip(samples_f, -1.0, 1.0) * 32767.0).astype(np.int16)

    return Fixture(
        path=str(path),
        pcm16=samples.tobytes(),
        speech_end_sample=end_16k,
        source_sample_rate=source_rate,
    )


def encode_fixture(fixture: Fixture, auto_silence_ms: int) -> tuple[list[bytes], bytes, int]:
    try:
        import opuslib_next
    except Exception as exc:
        raise BenchmarkError(f"Opus dependency unavailable: {exc}") from exc

    encoder = opuslib_next.Encoder(INPUT_RATE, 1, opuslib_next.APPLICATION_VOIP)
    pcm = fixture.pcm16
    frame_bytes = FRAME_SAMPLES * 2
    frames: list[bytes] = []
    for offset in range(0, len(pcm), frame_bytes):
        chunk = pcm[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk += b"\x00" * (frame_bytes - len(chunk))
        frames.append(encoder.encode(chunk, FRAME_SAMPLES))
    silence_frame = encoder.encode(b"\x00" * frame_bytes, FRAME_SAMPLES)
    silence_count = max(1, math.ceil(max(0, auto_silence_ms) / FRAME_MS))
    return frames, silence_frame, silence_count


async def _paced_send(ws, frames: Iterable[bytes], started: float) -> None:
    for index, frame in enumerate(frames):
        target = started + index * FRAME_MS / 1000.0
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        await ws.send(frame)


async def _receive_turn(ws, trace: RunTrace, timeout_seconds: float) -> None:
    deadline = time.perf_counter() + timeout_seconds
    while "tts_stop_received" not in trace.marks:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise asyncio.TimeoutError
        message = await asyncio.wait_for(ws.recv(), timeout=remaining)
        now = time.perf_counter()
        if isinstance(message, bytes):
            trace.received_audio_frames += 1
            trace.mark("first_binary_received", now)
            continue
        data = json.loads(message)
        msg_type = data.get("type")
        if msg_type == "stt" and data.get("speech_final") is True:
            trace.transcript = str(data.get("text") or "").strip()
            trace.mark("stt_final_received", now)
        elif msg_type == "vad" and data.get("state") == "speech_ended":
            trace.mark("vad_speech_ended_received", now)
        elif msg_type == "tts":
            state = data.get("state")
            if state == "start":
                trace.mark("tts_start_received", now)
            elif state == "sentence_start":
                trace.mark("first_clause_received", now)
            elif state == "stop":
                trace.mark("tts_stop_received", now)


async def _hello(ws, mode: str, timeout_seconds: float) -> str:
    await ws.send(json.dumps({
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": INPUT_RATE,
            "channels": 1,
            "frame_duration": FRAME_MS,
        },
    }))
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
    if not isinstance(raw, str):
        raise BenchmarkError("server hello was not text JSON")
    hello = json.loads(raw)
    session_id = str(hello.get("session_id") or "")
    if hello.get("type") != "hello" or not session_id:
        raise BenchmarkError(f"invalid server hello: {hello!r}")
    return session_id


async def _run_turn(
    ws,
    *,
    session_id: str,
    fixture: Fixture,
    encoded_frames: list[bytes],
    silence_frame: bytes,
    silence_count: int,
    run_index: int,
    measured: bool,
    mode: str,
    timeout_seconds: float,
) -> RunTrace:
    trace = RunTrace(run_index=run_index, measured=measured, mode=mode, session_id=session_id)
    receiver = asyncio.create_task(_receive_turn(ws, trace, timeout_seconds))
    try:
        await ws.send(json.dumps({
            "type": "listen",
            "state": "start",
            "mode": mode,
            "session_id": session_id,
        }))
        audio_started = time.perf_counter()
        trace.mark("audio_start", audio_started)
        trace.mark(
            "speech_end",
            audio_started + fixture.speech_end_sample / INPUT_RATE,
        )
        await _paced_send(ws, encoded_frames, audio_started)

        if mode == "auto":
            tail_started = audio_started + len(encoded_frames) * FRAME_MS / 1000.0
            await _paced_send(
                ws,
                [silence_frame] * silence_count,
                tail_started,
            )
            trace.mark("auto_silence_sent")
        else:
            await ws.send(json.dumps({
                "type": "listen",
                "state": "stop",
                "session_id": session_id,
            }))
            trace.mark("listen_stop_sent")

        await receiver
        if not trace.transcript:
            raise BenchmarkError("missing final STT transcript")
        if trace.received_audio_frames <= 0:
            raise BenchmarkError("missing TTS binary audio")
        first_audio = trace.marks.get("first_binary_received")
        if first_audio is not None and first_audio < trace.marks["speech_end"]:
            raise BenchmarkError("first response audio arrived before labelled speech end")
        trace.outcome = "completed"
    except asyncio.TimeoutError:
        trace.outcome = "timeout"
        trace.error = "turn timeout"
    except asyncio.CancelledError:
        trace.outcome = "cancelled"
        trace.error = "cancelled"
        raise
    except Exception as exc:
        trace.outcome = "failed"
        trace.error = f"{type(exc).__name__}: {exc}"
    finally:
        if not receiver.done():
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass
    return trace


def _percentile(values: list[float], quantile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[rank], 3)


def summarize(records: list[Dict[str, Any]]) -> Dict[str, Any]:
    measured = [record for record in records if record["measured"]]
    values = [
        float(record["speech_end_to_first_audio_received_ms"])
        for record in measured
        if record["outcome"] == "completed"
        and record["speech_end_to_first_audio_received_ms"] is not None
    ]
    outcomes: Dict[str, int] = {}
    for record in measured:
        outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
    return {
        "metric": "speech_end_to_first_audio_received_ms",
        "requested_samples": len(measured),
        "valid_metric_samples": len(values),
        "missing_metric_samples": len(measured) - len(values),
        "outcomes": outcomes,
        "p50_ms": round(statistics.median(values), 3) if values else None,
        "p90_ms": _percentile(values, 0.90),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 3) if values else None,
        "sla_p95_lt_1000_ms": bool(values) and _percentile(values, 0.95) < 1000.0,
        "target_p50_lte_600_ms": bool(values) and statistics.median(values) <= 600.0,
    }


async def run(args) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    try:
        import websockets
    except Exception as exc:
        raise BenchmarkError(f"websockets dependency unavailable: {exc}") from exc

    fixture = load_fixture(Path(args.wav), args.speech_end_sample)
    encoded_frames, silence_frame, silence_count = encode_fixture(
        fixture,
        args.auto_silence_ms,
    )
    headers = {
        "Device-Id": "veetee-benchmark-device",
        "Client-Id": "veetee-benchmark-client",
        "Protocol-Version": "1",
    }
    total_runs = args.warmup + args.runs
    records: list[Dict[str, Any]] = []

    async def run_on_connection(ws, session_id: str, index: int) -> None:
        trace = await _run_turn(
            ws,
            session_id=session_id,
            fixture=fixture,
            encoded_frames=encoded_frames,
            silence_frame=silence_frame,
            silence_count=silence_count,
            run_index=index,
            measured=index >= args.warmup,
            mode=args.mode,
            timeout_seconds=args.timeout,
        )
        records.append(trace.record())

    if args.reconnect_each:
        for index in range(total_runs):
            async with websockets.connect(args.uri, additional_headers=headers) as ws:
                session_id = await _hello(ws, args.mode, args.timeout)
                await run_on_connection(ws, session_id, index)
    else:
        async with websockets.connect(args.uri, additional_headers=headers) as ws:
            session_id = await _hello(ws, args.mode, args.timeout)
            for index in range(total_runs):
                await run_on_connection(ws, session_id, index)

    summary = summarize(records)
    summary.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uri": args.uri,
        "mode": args.mode,
        "warmup_runs": args.warmup,
        "reconnect_each": args.reconnect_each,
        "fixture": fixture.path,
        "fixture_source_sample_rate": fixture.source_sample_rate,
        "speech_end_sample_16k": fixture.speech_end_sample,
        "auto_silence_ms": args.auto_silence_ms if args.mode == "auto" else None,
    })
    return records, summary


def parse_args():
    parser = argparse.ArgumentParser(
        description="Benchmark stock Xiaozhi voice latency from labelled fixture speech-end"
    )
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True, help="pre-generated mono PCM16 WAV fixture")
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument("--mode", choices=("auto", "manual"), default="auto")
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--auto-silence-ms", type=int, default=720)
    parser.add_argument("--reconnect-each", action="store_true")
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1 or args.warmup < 0 or args.timeout <= 0:
        print("invalid runs/warmup/timeout", file=sys.stderr)
        return 2
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw_path = artifact_dir / f"pipeline-{args.mode}-{stamp}.jsonl"
    summary_path = artifact_dir / f"pipeline-{args.mode}-{stamp}.summary.json"
    try:
        records, summary = asyncio.run(run(args))
    except BenchmarkError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    with raw_path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"raw_jsonl={raw_path}")
    print(f"summary_json={summary_path}")
    return 0 if summary["outcomes"].get("completed", 0) == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
