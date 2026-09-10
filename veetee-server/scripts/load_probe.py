#!/usr/bin/env python3
"""Concurrent text-turn load probe: 1/2/4 sessions against the live server.

Each worker holds one WebSocket session and runs K sequential text turns.
Measures per-turn latency (text sent -> tts:stop), failures and active
session count. This exercises LLM/TTS/context paths concurrently; it does
NOT load ASR audio decode (text turns) — report that limitation alongside
any numbers. Not a certification gate, only a capacity snapshot.

Exit codes: 0 ran to completion (report may still show failures),
2 BLOCKED (server not ready).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.request

QUESTIONS = [
    "Mấy giờ rồi?",
    "Hôm nay là thứ mấy?",
    "Tính giúp tôi 17 nhân 23.",
    "Kể một câu thật ngắn về con mèo.",
    "Thời tiết hôm nay thế nào?",
]


async def one_turn(ws, text: str, timeout: float) -> tuple[bool, float, int]:
    """Send one text turn; return (completed, latency_s, binaries)."""
    start = time.monotonic()
    await ws.send(json.dumps({"type": "text", "text": text}))
    binaries = 0
    try:
        while time.monotonic() - start < timeout:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(msg, bytes):
                binaries += 1
                continue
            try:
                item = json.loads(msg)
            except Exception:
                continue
            if item.get("type") == "tts" and item.get("state") == "stop":
                return True, time.monotonic() - start, binaries
    except (asyncio.TimeoutError, ConnectionError):
        return False, time.monotonic() - start, binaries
    return False, time.monotonic() - start, binaries


async def worker(uri: str, turns: int, timeout: float) -> dict:
    from websockets.asyncio.client import connect

    latencies: list[float] = []
    completed = failed = binaries = 0
    try:
        async with connect(uri, max_size=8 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "hello", "version": 1, "transport": "websocket",
                "audio_params": {"format": "opus", "sample_rate": 16000,
                                 "channels": 1, "frame_duration": 60}}))
            await asyncio.wait_for(ws.recv(), timeout=10)
            for i in range(turns):
                ok, latency, nb = await one_turn(
                    ws, QUESTIONS[i % len(QUESTIONS)], timeout)
                binaries += nb
                if ok:
                    completed += 1
                    latencies.append(latency)
                else:
                    failed += 1
    except Exception as exc:  # connect-level failure fails all turns
        failed += turns
        return {"completed": 0, "failed": failed, "latencies": [],
                "binaries": 0, "error": str(exc)[:120]}
    return {"completed": completed, "failed": failed,
            "latencies": latencies, "binaries": binaries}


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(pct / 100 * len(ordered)))
    return ordered[index]


async def run_level(uri: str, sessions: int, turns: int,
                    timeout: float) -> dict:
    results = await asyncio.gather(
        *(worker(uri, turns, timeout) for _ in range(sessions)))
    latencies = [lat for r in results for lat in r["latencies"]]
    completed = sum(r["completed"] for r in results)
    failed = sum(r["failed"] for r in results)
    return {
        "sessions": sessions,
        "turns_requested": sessions * turns,
        "completed": completed,
        "failed": failed,
        "success_rate": completed / (sessions * turns) if sessions * turns else 0,
        "p50_s": percentile(latencies, 50),
        "p95_s": percentile(latencies, 95),
        "max_s": max(latencies) if latencies else None,
        "binaries": sum(r["binaries"] for r in results),
        "errors": [r["error"] for r in results if "error" in r],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--health", default="http://127.0.0.1:8003/health")
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    try:
        with urllib.request.urlopen(args.health, timeout=10) as resp:
            status = json.load(resp)
    except Exception as exc:
        print(f"BLOCKED: server unhealthy: {exc}", flush=True)
        return 2
    if status.get("readiness") != "ready":
        print(f"BLOCKED: server not ready: {status}", flush=True)
        return 2

    report = {"levels": [], "note": "text turns only; no ASR audio decode load"}
    for level in (int(x) for x in args.levels.split(",") if x.strip()):
        print(f"--- level: {level} sessions x {args.turns} turns ---",
              flush=True)
        result = asyncio.run(run_level(args.uri, level, args.turns,
                                       args.timeout))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        report["levels"].append(result)
    print("=== LOAD REPORT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
