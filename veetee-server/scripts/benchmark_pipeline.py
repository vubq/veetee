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
import struct
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
DEFAULT_VOICED_RMS_THRESHOLD = 160.0


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
    decoded_audio_frames: int = 0
    first_voiced_rms: Optional[float] = None
    leading_output_silence_ms: Optional[float] = None
    first_tts_sentence: str = ""
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
            "decoded_audio_frames": self.decoded_audio_frames,
            "first_voiced_rms": self.first_voiced_rms,
            "leading_output_silence_ms": self.leading_output_silence_ms,
            "first_tts_sentence": self.first_tts_sentence,
            "speech_end_to_stt_final_ms": self.delta_ms("speech_end", "stt_final_received"),
            "speech_end_to_first_clause_ms": self.delta_ms("speech_end", "first_clause_received"),
            "speech_end_to_first_binary_received_ms": self.delta_ms("speech_end", "first_binary_received"),
            "speech_end_to_first_voiced_pcm_received_ms": self.delta_ms(
                "speech_end", "first_voiced_pcm_received"
            ),
            # Legacy packet metric retained for old artifact readers. It is not
            # used for SLA because an Opus packet may decode to silence.
            "speech_end_to_first_audio_received_ms": self.delta_ms("speech_end", "first_binary_received"),
            "first_audio_to_tts_stop_ms": self.delta_ms("first_binary_received", "tts_stop_received"),
            "first_voiced_pcm_to_tts_stop_ms": self.delta_ms(
                "first_voiced_pcm_received", "tts_stop_received"
            ),
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


def _first_voiced_sample_offset(
    pcm16: bytes,
    *,
    sample_rate: int,
    rms_threshold: float,
    window_ms: int = 10,
) -> tuple[Optional[int], Optional[float]]:
    """Return the first 10 ms window whose PCM16 RMS crosses the threshold."""
    if not pcm16:
        return None, None
    if len(pcm16) % 2:
        raise BenchmarkError("decoded PCM16 frame has odd byte length")

    samples = [item[0] for item in struct.iter_unpack("<h", pcm16)]
    window_samples = max(1, int(sample_rate * window_ms / 1000))
    for start in range(0, len(samples), window_samples):
        window = samples[start : start + window_samples]
        if not window:
            break
        rms = math.sqrt(sum(float(value) * float(value) for value in window) / len(window))
        if rms >= rms_threshold:
            return start, round(rms, 3)
    return None, None


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


async def _receive_turn(
    ws,
    trace: RunTrace,
    timeout_seconds: float,
    *,
    response_decoder,
    response_sample_rate: int,
    response_frame_samples: int,
    voiced_rms_threshold: float,
) -> None:
    deadline = time.perf_counter() + timeout_seconds
    decoded_samples_before_voice = 0
    while "tts_stop_received" not in trace.marks:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise asyncio.TimeoutError
        message = await asyncio.wait_for(ws.recv(), timeout=remaining)
        now = time.perf_counter()
        if isinstance(message, bytes):
            trace.received_audio_frames += 1
            trace.mark("first_binary_received", now)
            try:
                pcm16 = response_decoder.decode(message, response_frame_samples)
            except Exception as exc:
                raise BenchmarkError(f"response Opus decode failed: {exc}") from exc
            trace.decoded_audio_frames += 1
            if "first_voiced_pcm_received" not in trace.marks:
                offset, rms = _first_voiced_sample_offset(
                    pcm16,
                    sample_rate=response_sample_rate,
                    rms_threshold=voiced_rms_threshold,
                )
                if offset is None:
                    decoded_samples_before_voice += len(pcm16) // 2
                else:
                    trace.first_voiced_rms = rms
                    trace.leading_output_silence_ms = round(
                        (decoded_samples_before_voice + offset) * 1000.0 / response_sample_rate,
                        3,
                    )
                    trace.mark("first_voiced_pcm_received", now)
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
                if not trace.first_tts_sentence:
                    trace.first_tts_sentence = str(data.get("text") or "").strip()
                trace.mark("first_clause_received", now)
            elif state == "stop":
                trace.mark("tts_stop_received", now)


async def _hello(ws, mode: str, timeout_seconds: float) -> tuple[str, int, int]:
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
    audio_params = hello.get("audio_params") or {}
    if str(audio_params.get("format") or "opus").lower() != "opus":
        raise BenchmarkError(f"unsupported response audio format: {audio_params!r}")
    try:
        sample_rate = int(audio_params.get("sample_rate") or 0)
        frame_duration_ms = int(audio_params.get("frame_duration") or FRAME_MS)
        channels = int(audio_params.get("channels") or 1)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"invalid response audio params: {audio_params!r}") from exc
    if sample_rate <= 0 or frame_duration_ms <= 0 or channels != 1:
        raise BenchmarkError(f"invalid response audio params: {audio_params!r}")
    return session_id, sample_rate, frame_duration_ms


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
    response_decoder,
    response_sample_rate: int,
    response_frame_duration_ms: int,
    voiced_rms_threshold: float,
) -> RunTrace:
    trace = RunTrace(run_index=run_index, measured=measured, mode=mode, session_id=session_id)
    receiver = asyncio.create_task(
        _receive_turn(
            ws,
            trace,
            timeout_seconds,
            response_decoder=response_decoder,
            response_sample_rate=response_sample_rate,
            response_frame_samples=int(response_sample_rate * response_frame_duration_ms / 1000),
            voiced_rms_threshold=voiced_rms_threshold,
        )
    )
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
        if "first_voiced_pcm_received" not in trace.marks:
            raise BenchmarkError("TTS binary decoded but no voiced PCM was detected")
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


METRIC_VERSION = "v2"
# Certification gates from the plan: quick smoke runs must never certify SLA.
CERT_MIN_SAMPLES = 100
CERT_MIN_SUCCESS_RATE = 0.99


def summarize(records: list[Dict[str, Any]], *, certification: bool = False) -> Dict[str, Any]:
    measured = [record for record in records if record["measured"]]
    values = [
        float(record["speech_end_to_first_voiced_pcm_received_ms"])
        for record in measured
        if record["outcome"] == "completed"
        and record.get("speech_end_to_first_voiced_pcm_received_ms") is not None
    ]
    outcomes: Dict[str, int] = {}
    for record in measured:
        outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
    successful_samples = sum(
        1
        for record in measured
        if record["outcome"] == "completed"
        and record.get("speech_end_to_first_voiced_pcm_received_ms") is not None
    )
    timeout_samples = outcomes.get("timeout", 0)
    cancelled_samples = outcomes.get("cancelled", 0)
    failed_samples = len(measured) - successful_samples - timeout_samples - cancelled_samples
    # Success is counted over ALL required attempts, including timeouts and
    # failures. Missing marks never pass. Quick smoke runs use the same math
    # but cannot certify: they report SMOKE_ONLY.
    minimum_samples = CERT_MIN_SAMPLES if certification else 20
    minimum_success_rate = CERT_MIN_SUCCESS_RATE if certification else 0.95
    success_rate = successful_samples / len(measured) if measured else 0.0
    sample_gate = len(measured) >= minimum_samples
    success_gate = success_rate >= minimum_success_rate
    p95 = _percentile(values, 0.95)
    p50 = round(statistics.median(values), 3) if values else None
    sla_pass = bool(values) and sample_gate and success_gate and (p95 or 0) < 1000.0
    p50_pass = bool(values) and sample_gate and success_gate and (p50 or 0) <= 600.0
    if not certification:
        sla_status = "SMOKE_ONLY"
    elif not (sample_gate and success_gate):
        sla_status = "NOT_MET"
    elif sla_pass and p50_pass:
        sla_status = "ACHIEVED"
    elif sla_pass:
        sla_status = "PARTIAL"
    else:
        sla_status = "NOT_MET"
    return {
        "metric": "speech_end_to_first_voiced_pcm_received_ms",
        "metric_version": METRIC_VERSION,
        # speech_end_to_first_useful_voiced_audio_received_ms is the
        # certification metric: first voiced audio of the USEFUL answer from
        # speech-end. The existing voiced-PCM field is only an acoustic proxy
        # and counts after a case passes quality/grounding. Benchmark runs
        # without a quality rubric leave useful=null and report PROXY_ONLY.
        "certification_metric": "speech_end_to_first_useful_voiced_audio_received_ms",
        "certification_mode": bool(certification),
        "requested_samples": len(measured),
        "successful_samples": successful_samples,
        "timeout_samples": timeout_samples,
        "cancelled_samples": cancelled_samples,
        "failed_samples": failed_samples,
        "valid_metric_samples": len(values),
        "missing_metric_samples": len(measured) - len(values),
        "minimum_samples": minimum_samples,
        "minimum_success_rate": minimum_success_rate,
        "success_rate": round(success_rate, 4),
        "sample_gate_passed": sample_gate,
        "success_rate_gate_passed": success_gate,
        "outcomes": outcomes,
        "p50_ms": p50,
        "p90_ms": _percentile(values, 0.90),
        "p95_ms": p95,
        "max_ms": round(max(values), 3) if values else None,
        "sla_p95_lt_1000_ms": sla_pass,
        "target_p50_lte_600_ms": p50_pass,
        "sla_status": sla_status,
        "note": (
            "p95 values are end-to-end percentiles; stage p95s are never summed. "
            "Voiced PCM is a proxy until quality/grounding passes."
        ),
    }


async def run(args) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    try:
        import opuslib_next
        import websockets
    except Exception as exc:
        raise BenchmarkError(f"benchmark dependency unavailable: {exc}") from exc

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

    async def run_on_connection(
        ws,
        session_id: str,
        response_decoder,
        response_sample_rate: int,
        response_frame_duration_ms: int,
        index: int,
    ) -> None:
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
            response_decoder=response_decoder,
            response_sample_rate=response_sample_rate,
            response_frame_duration_ms=response_frame_duration_ms,
            voiced_rms_threshold=args.voiced_rms_threshold,
        )
        records.append(trace.record())

    if args.reconnect_each:
        for index in range(total_runs):
            async with websockets.connect(args.uri, additional_headers=headers) as ws:
                session_id, response_sample_rate, response_frame_duration_ms = await _hello(
                    ws, args.mode, args.timeout
                )
                response_decoder = opuslib_next.Decoder(response_sample_rate, 1)
                await run_on_connection(
                    ws,
                    session_id,
                    response_decoder,
                    response_sample_rate,
                    response_frame_duration_ms,
                    index,
                )
    else:
        async with websockets.connect(args.uri, additional_headers=headers) as ws:
            session_id, response_sample_rate, response_frame_duration_ms = await _hello(
                ws, args.mode, args.timeout
            )
            response_decoder = opuslib_next.Decoder(response_sample_rate, 1)
            for index in range(total_runs):
                await run_on_connection(
                    ws,
                    session_id,
                    response_decoder,
                    response_sample_rate,
                    response_frame_duration_ms,
                    index,
                )

    summary = summarize(records, certification=bool(getattr(args, "certification", False)))
    summary.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "uri": args.uri,
        "mode": args.mode,
        "warmup_runs": args.warmup,
        "reconnect_each": args.reconnect_each,
        "fixture": fixture.path,
        "fixture_source_sample_rate": fixture.source_sample_rate,
        "speech_end_sample_16k": fixture.speech_end_sample,
        "voiced_rms_threshold_pcm16": args.voiced_rms_threshold,
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
    parser.add_argument(
        "--voiced-rms-threshold",
        type=float,
        default=DEFAULT_VOICED_RMS_THRESHOLD,
        help="PCM16 RMS threshold used to identify the first voiced 10 ms output window",
    )
    parser.add_argument("--reconnect-each", action="store_true")
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--certification", action="store_true",
                        help="use 100-attempt/99%% certification gates instead of smoke gates")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1 or args.warmup < 0 or args.timeout <= 0 or args.voiced_rms_threshold <= 0:
        print("invalid runs/warmup/timeout/voiced-rms-threshold", file=sys.stderr)
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
