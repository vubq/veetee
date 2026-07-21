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
DEVICE_TOOLS = [
    {
        "name": "self.get_device_status",
        "description": (
            "Read the current device state, assistant gate, firmware version and "
            "speaker volume."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
    {
        "name": "self.audio_speaker.get_volume",
        "description": (
            "Read the current speaker output volume as a percentage from 0 to 100."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
    },
    {
        "name": "self.audio_speaker.set_volume",
        "description": "Set speaker output volume from 0 to 100 percent.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["volume"],
            "properties": {
                "volume": {"type": "integer", "minimum": 0, "maximum": 100}
            },
        },
    },
]


def load_pcm(path: Path, sample_rate: int) -> bytes:
    audio, source_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = audio[:, 0]
    if source_rate != sample_rate:
        mono = soxr.resample(mono, source_rate, sample_rate, quality="HQ")
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2")
    trailing_silence = np.zeros(sample_rate, dtype="<i2")
    return pcm.tobytes() + trailing_silence.tobytes()


async def run(
    url: str,
    wav_path: Path,
    timeout: float,
    *,
    abort_on_first_audio: bool,
    abort_on_tool_call: bool,
    late_tool_result_delay: float,
    expected_tool: str | None,
    expected_volume: int | None,
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
        await bootstrap_device_mcp(websocket, session_id, timeout)
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
        pending_tool_result_id: int | str | None = None
        late_tool_result_sent = False
        observe_after_late_until: float | None = None
        stale_output_after_abort: list[str] = []
        while True:
            if saw_tts_stop and not abort_on_first_audio and not abort_on_tool_call:
                break
            if saw_tts_stop and listening_after_abort and abort_on_first_audio:
                break
            if (
                abort_on_tool_call
                and observe_after_late_until is not None
                and perf_counter() >= observe_after_late_until
            ):
                break
            receive_timeout = timeout
            if observe_after_late_until is not None:
                receive_timeout = max(
                    0.01,
                    min(timeout, observe_after_late_until - perf_counter()),
                )
            try:
                message = await asyncio.wait_for(
                    websocket.recv(), timeout=receive_timeout
                )
            except TimeoutError:
                if (
                    abort_on_tool_call
                    and observe_after_late_until is not None
                    and perf_counter() >= observe_after_late_until
                ):
                    break
                result["timeout"] = True
                break
            elapsed_ms = (perf_counter() - started) * 1000
            if isinstance(message, bytes):
                result["downlink_frames"] += 1
                timings.setdefault("first_audio_ms", elapsed_ms)
                if abort_sent:
                    frames_after_abort += 1
                    if abort_on_tool_call:
                        stale_output_after_abort.append("audio")
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
            if event_type == "mcp":
                handled = await handle_device_mcp(
                    websocket,
                    event,
                    session_id,
                    respond=not abort_on_tool_call,
                )
                if handled is not None:
                    tool_call, request_id = handled
                    result.setdefault("tool_calls", []).append(tool_call)
                    if abort_on_tool_call:
                        if abort_sent:
                            stale_output_after_abort.append("tools/call")
                        else:
                            pending_tool_result_id = request_id
                            await websocket.send(
                                json.dumps(
                                    {
                                        "session_id": session_id,
                                        "type": "abort",
                                        "reason": "e2e_mcp_interrupt",
                                        "source": "button",
                                    },
                                    separators=(",", ":"),
                                )
                            )
                            abort_sent = True
                            timings["abort_sent_ms"] = elapsed_ms
                continue
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
                if abort_sent and abort_on_tool_call:
                    stale_output_after_abort.append("llm:text")
                result["llm_characters"] = result.get("llm_characters", 0) + len(
                    event["text"]
                )
                result.setdefault("llm_text", []).append(event["text"])
            elif event_type == "tts" and state == "start":
                if abort_sent and abort_on_tool_call:
                    stale_output_after_abort.append("tts:start")
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
                    if (
                        abort_on_tool_call
                        and pending_tool_result_id is not None
                        and not late_tool_result_sent
                    ):
                        await asyncio.sleep(late_tool_result_delay)
                        await send_mcp_result(
                            websocket,
                            session_id,
                            pending_tool_result_id,
                            {
                                "content": [{"type": "text", "text": "late"}],
                                "isError": False,
                            },
                        )
                        late_tool_result_sent = True
                        timings["late_tool_result_ms"] = (
                            perf_counter() - started
                        ) * 1000
                        observe_after_late_until = perf_counter() + 1.0
                elif not saw_tts_start:
                    result["ended_without_tts"] = True
                    break

    result["timings"] = {key: round(value, 2) for key, value in timings.items()}
    if abort_on_tool_call:
        abort_to_listening_ms = (
            timings.get("listening_after_abort_ms", float("inf"))
            - timings.get("abort_sent_ms", 0.0)
        )
        result["late_tool_result_sent"] = late_tool_result_sent
        result["stale_output_after_abort"] = stale_output_after_abort
        result["abort_to_listening_ms"] = round(abort_to_listening_ms, 2)
        result["ok"] = all(
            (
                saw_stt,
                saw_thinking,
                bool(result.get("tool_calls")),
                abort_sent,
                listening_after_abort,
                late_tool_result_sent,
                abort_to_listening_ms <= 250.0,
                not saw_tts_start,
                not stale_output_after_abort,
            )
        )
    elif abort_on_first_audio:
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
    if expected_tool is not None:
        calls = result.get("tool_calls", [])
        result["ok"] = bool(result["ok"]) and any(
            call.get("name") == expected_tool
            and (
                expected_volume is None
                or call.get("arguments", {}).get("volume") == expected_volume
            )
            for call in calls
        )
    return result


async def bootstrap_device_mcp(
    websocket: Any, session_id: str, timeout: float
) -> None:
    initialize = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
    payload = initialize.get("payload", {})
    if initialize.get("type") != "mcp" or payload.get("method") != "initialize":
        raise RuntimeError("Expected MCP initialize after server hello")
    await send_mcp_result(
        websocket,
        session_id,
        payload["id"],
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "veetee-e2e-device", "version": "test"},
        },
    )

    tools_list = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
    payload = tools_list.get("payload", {})
    if tools_list.get("type") != "mcp" or payload.get("method") != "tools/list":
        raise RuntimeError("Expected MCP tools/list after initialize")
    await send_mcp_result(
        websocket,
        session_id,
        payload["id"],
        {"tools": DEVICE_TOOLS, "nextCursor": ""},
    )


async def handle_device_mcp(
    websocket: Any,
    event: dict[str, Any],
    session_id: str,
    *,
    respond: bool = True,
) -> tuple[dict[str, Any], int | str] | None:
    payload = event.get("payload", {})
    if payload.get("method") != "tools/call":
        return None
    params = payload.get("params", {})
    name = params.get("name")
    arguments = params.get("arguments", {})
    if name not in {tool["name"] for tool in DEVICE_TOOLS}:
        await send_mcp_error(websocket, session_id, payload["id"], -32601, "Unknown tool")
        return None
    request_id = payload.get("id")
    if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
        raise RuntimeError("Device MCP tools/call request is missing a valid id")
    if respond:
        await send_mcp_result(
            websocket,
            session_id,
            request_id,
            {"content": [{"type": "text", "text": "true"}], "isError": False},
        )
    return {"name": name, "arguments": arguments}, request_id


async def send_mcp_result(
    websocket: Any, session_id: str, request_id: int | str, result: dict[str, Any]
) -> None:
    await websocket.send(
        json.dumps(
            {
                "session_id": session_id,
                "type": "mcp",
                "payload": {"jsonrpc": "2.0", "id": request_id, "result": result},
            },
            separators=(",", ":"),
        )
    )


async def send_mcp_error(
    websocket: Any,
    session_id: str,
    request_id: int | str,
    code: int,
    message: str,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "session_id": session_id,
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": code, "message": message},
                },
            },
            separators=(",", ":"),
        )
    )


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
    parser.add_argument("--abort-on-tool-call", action="store_true")
    parser.add_argument("--late-tool-result-delay", type=float, default=0.25)
    parser.add_argument("--expect-tool")
    parser.add_argument("--expected-volume", type=int, choices=range(0, 101))
    args = parser.parse_args()
    if args.abort_on_first_audio and args.abort_on_tool_call:
        parser.error("Choose only one abort scenario")
    if args.late_tool_result_delay < 0:
        parser.error("--late-tool-result-delay must be non-negative")
    result = asyncio.run(
        run(
            args.url,
            args.wav,
            args.timeout,
            abort_on_first_audio=args.abort_on_first_audio,
            abort_on_tool_call=args.abort_on_tool_call,
            late_tool_result_delay=args.late_tool_result_delay,
            expected_tool=args.expect_tool,
            expected_volume=args.expected_volume,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
