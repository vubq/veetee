import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Dict


class E2EFailure(RuntimeError):
    pass


class E2EBlocked(RuntimeError):
    pass


@dataclass
class E2ETrace:
    session_id: str = ""
    stt_text: str = ""
    received_audio_frames: int = 0
    marks: Dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.perf_counter())


def validate_trace(trace: E2ETrace) -> None:
    if not trace.session_id:
        raise E2EFailure("missing valid server hello/session_id")
    if not trace.stt_text.strip():
        raise E2EFailure("missing final STT transcript")
    if trace.received_audio_frames <= 0:
        raise E2EFailure("missing TTS binary audio")

    required = (
        "speech_audio_end_sent",
        "listen_stop_sent",
        "stt_final_received",
        "vad_speech_ended_received",
        "tts_start_received",
        "first_clause_received",
        "first_binary_received",
        "tts_stop_received",
    )
    missing = [name for name in required if name not in trace.marks]
    if missing:
        raise E2EFailure(f"missing required E2E markers: {', '.join(missing)}")

    ordered = (
        "stt_final_received",
        "vad_speech_ended_received",
        "tts_start_received",
        "first_clause_received",
        "first_binary_received",
        "tts_stop_received",
    )
    for previous, current in zip(ordered, ordered[1:]):
        if trace.marks[current] < trace.marks[previous]:
            raise E2EFailure(f"wrong event order: {current} arrived before {previous}")


def _format_delta(trace: E2ETrace, start: str, end: str) -> str:
    if start not in trace.marks or end not in trace.marks:
        return "n/a"
    return f"{(trace.marks[end] - trace.marks[start]) * 1000:.0f} ms"


def print_trace_summary(trace: E2ETrace) -> None:
    print("\n=== E2E latency markers (client observed) ===")
    print(f"Speech audio end -> final STT: {_format_delta(trace, 'speech_audio_end_sent', 'stt_final_received')}")
    print(f"Speech audio end -> VAD speech_ended event: {_format_delta(trace, 'speech_audio_end_sent', 'vad_speech_ended_received')}")
    print(f"Speech audio end -> first LLM clause signal: {_format_delta(trace, 'speech_audio_end_sent', 'first_clause_received')}")
    print(f"Speech audio end -> first TTS binary received: {_format_delta(trace, 'speech_audio_end_sent', 'first_binary_received')}")
    print(f"Listen stop -> first TTS binary received: {_format_delta(trace, 'listen_stop_sent', 'first_binary_received')}")
    print(f"First TTS binary -> tts:stop: {_format_delta(trace, 'first_binary_received', 'tts_stop_received')}")
    print(f"TTS audio frames received: {trace.received_audio_frames}")
    print("Server logs separately report VAD endpoint reason, ASR correction/final stage, post-ASR first clause, first binary sent, and tts:stop sent.")


def synthesize_question_audio():
    try:
        import numpy as np
        import opuslib_next
        import soxr
        from vieneu import Vieneu
    except Exception as exc:
        raise E2EBlocked(f"speech synthesis dependencies unavailable: {exc}") from exc

    question_text = "Hà Nội là thủ đô của nước nào?"
    try:
        tts = Vieneu()
        audio_48k = tts.infer(question_text)
    except Exception as exc:
        raise E2EBlocked(f"VieNeu runtime/model unavailable: {exc}") from exc

    audio_16k_f = soxr.resample(audio_48k, 48000, 16000)
    audio_16k_i16 = (np.clip(audio_16k_f, -1.0, 1.0) * 32767.0).astype(np.int16)

    enc16 = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
    opus_frames = []
    pcm_bytes = audio_16k_i16.tobytes()
    frame_size_bytes = 960 * 2
    for offset in range(0, len(pcm_bytes), frame_size_bytes):
        chunk = pcm_bytes[offset:offset + frame_size_bytes]
        if len(chunk) == frame_size_bytes:
            opus_frames.append(enc16.encode(chunk, 960))

    if not opus_frames:
        raise E2EBlocked("VieNeu produced no complete 60 ms Opus input frames")

    silence_frame = enc16.encode(b"\x00\x00" * 960, 960)
    return question_text, opus_frames, silence_frame


async def run_full_speech_test(uri: str, timeout_seconds: float) -> E2ETrace:
    try:
        import websockets
    except Exception as exc:
        raise E2EBlocked(f"websockets dependency unavailable: {exc}") from exc

    print("=== STEP 1: Synthesizing test question audio using VieNeu ===")
    question_text, opus_frames, silence_frame = synthesize_question_audio()
    print(f"Query: {question_text!r}; {len(opus_frames)} speech frames (~{len(opus_frames) * 0.06:.2f}s)")

    trace = E2ETrace()
    headers = {
        "Device-Id": "00:11:22:33:44:55",
        "Client-Id": "veetee-e2e-stock-fw-contract",
        "Protocol-Version": "1",
    }

    print(f"\n=== STEP 2: Connecting to {uri} ===")
    try:
        connection = websockets.connect(uri, additional_headers=headers)
        async with connection as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            }))

            try:
                raw_hello = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise E2EFailure("timeout waiting for server hello") from exc
            if not isinstance(raw_hello, str):
                raise E2EFailure("server hello was not a JSON text message")
            try:
                hello = json.loads(raw_hello)
            except json.JSONDecodeError as exc:
                raise E2EFailure("server hello was invalid JSON") from exc
            if hello.get("type") != "hello" or not hello.get("session_id"):
                raise E2EFailure(f"invalid server hello: {hello!r}")
            trace.session_id = hello["session_id"]
            print(f"Server hello OK, session={trace.session_id}")

            print("\n=== STEP 3: Streaming simulated stock-FW microphone audio ===")
            await ws.send(json.dumps({
                "type": "listen",
                "state": "start",
                "session_id": trace.session_id,
            }))

            for frame in opus_frames:
                await ws.send(frame)
                await asyncio.sleep(0.06)
            trace.mark("speech_audio_end_sent")

            # 8 x 60 ms = 480 ms, enough to cross the 450 ms Silero setting
            # on 32 ms VAD frames without changing the stock client contract.
            for _ in range(8):
                await ws.send(silence_frame)
                await asyncio.sleep(0.06)

            await ws.send(json.dumps({
                "type": "listen",
                "state": "stop",
                "session_id": trace.session_id,
            }))
            trace.mark("listen_stop_sent")

            print("Waiting for final STT, VAD end, TTS start/clause/audio/stop...")
            deadline = time.perf_counter() + timeout_seconds
            while "tts_stop_received" not in trace.marks:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise E2EFailure("timeout before complete TTS response")
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise E2EFailure("timeout before complete TTS response") from exc

                if isinstance(msg, bytes):
                    trace.received_audio_frames += 1
                    trace.mark("first_binary_received")
                    continue

                try:
                    data = json.loads(msg)
                except json.JSONDecodeError as exc:
                    raise E2EFailure(f"invalid JSON message from server: {msg!r}") from exc

                msg_type = data.get("type")
                if msg_type == "stt" and data.get("speech_final") is True:
                    trace.stt_text = str(data.get("text") or "").strip()
                    trace.mark("stt_final_received")
                    print(f"Final STT: {trace.stt_text!r}")
                elif msg_type == "vad" and data.get("state") == "speech_ended":
                    trace.mark("vad_speech_ended_received")
                elif msg_type == "tts":
                    state = data.get("state")
                    if state == "start":
                        trace.mark("tts_start_received")
                    elif state == "sentence_start":
                        trace.mark("first_clause_received")
                    elif state == "stop":
                        trace.mark("tts_stop_received")

    except E2EFailure:
        raise
    except (ConnectionError, OSError) as exc:
        raise E2EBlocked(f"server/runtime unavailable at {uri}: {exc}") from exc

    validate_trace(trace)
    print_trace_summary(trace)
    return trace


def parse_args():
    parser = argparse.ArgumentParser(description="VeeTee stock-FW-compatible voice E2E check")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_full_speech_test(args.uri, max(args.timeout, 1.0)))
    except E2EBlocked as exc:
        print(f"\nBLOCKED: {exc}", file=sys.stderr)
        return 2
    except E2EFailure as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"\nFAILED: unexpected E2E error: {exc}", file=sys.stderr)
        return 1

    print("\nPASSED: required stock-protocol E2E markers and ordering verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
