#!/usr/bin/env python3
"""Benchmark VeeTee streaming-TTS queue depth under concurrent live turns.

This runner changes only the bounded internal VieNeu producer queue. Device
send-ahead/pacing remains unchanged. The original runtime value is restored
in a finally block.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )
    from scripts.load_probe import run_level
except ModuleNotFoundError:
    from benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )
    from load_probe import run_level


DEFAULT_LONG_QUESTION = (
    "Hãy trả lời bằng tiếng Việt trong khoảng 180 đến 220 ký tự. "
    "Giải thích ngắn gọn vì sao trợ lý giọng nói cần phản hồi nhanh nhưng vẫn "
    "phải giữ câu trả lời tự nhiên và chính xác."
)


def parse_values(raw: str) -> list[int]:
    values: list[int] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise MatrixError(f"invalid TTS queue value: {part!r}") from exc
        if not 1 <= value <= 256:
            raise MatrixError("TTS queue values must be between 1 and 256")
        if value not in values:
            values.append(value)
    if not values:
        raise MatrixError("TTS queue matrix is empty")
    return values


async def runtime_value(http_base: str, token: str) -> int:
    diagnostics = await request_json(
        "GET",
        f"{http_base.rstrip('/')}/api/diagnostics",
        token=token,
    )
    value = diagnostics.get("profile", {}).get("tts", {}).get(
        "stream_queue_max_chunks"
    )
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MatrixError(
            "diagnostics missing tts.stream_queue_max_chunks"
        ) from exc


async def apply_value(http_base: str, token: str, value: int) -> None:
    result = await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={"values": {"tts.stream_queue_max_chunks": int(value)}},
        timeout=30.0,
    )
    if not result.get("applied", False):
        raise MatrixError(f"runtime did not confirm queue value {value}")


def row_from_result(value: int, result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("server_metrics") or {}
    return {
        "value_chunks": int(value),
        "success_rate": result.get("success_rate"),
        "protocol_success_rate": result.get("protocol_success_rate"),
        "trace_complete": result.get("trace_complete"),
        "turn_start_to_first_ws_binary_ms": metrics.get(
            "turn_start_to_first_ws_binary_ms"
        ),
        "tts_queue_to_lock_ms": metrics.get("tts_queue_to_lock"),
        "tts_first_pcm_ms": metrics.get("tts_first_pcm"),
        "tts_lease_held_ms": metrics.get("tts_lease_held"),
        "tts_queue_wait_max_ms": metrics.get("tts_queue_wait_max"),
        "tts_queue_wait_total_ms": metrics.get("tts_queue_wait_total"),
        "tts_lease_held_max_ms": metrics.get("tts_lease_held_max"),
        "tts_lease_held_total_ms": metrics.get("tts_lease_held_total"),
        "tts_inference_max_ms": metrics.get("tts_inference_max"),
        "tts_inference_total_ms": metrics.get("tts_inference_total"),
        "tts_segment_count": metrics.get("tts_segment_count"),
        "outcomes": metrics.get("outcomes"),
    }


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    http_base = args.health.rsplit("/health", 1)[0].rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=30.0)
    token = load_management_token(args)
    values = parse_values(args.values)
    original = await runtime_value(http_base, token)

    rows: list[dict[str, Any]] = []
    restore_error = ""
    try:
        for index, value in enumerate(values):
            if value != await runtime_value(http_base, token):
                await apply_value(http_base, token, value)
            if index > 0 and args.variant_cooldown_ms > 0:
                await asyncio.sleep(args.variant_cooldown_ms / 1000.0)
            result = await run_level(
                args.uri,
                args.sessions,
                args.turns,
                args.timeout,
                http_base=http_base,
                management_token=token,
                turn_cooldown_ms=args.turn_cooldown_ms,
                metrics_settle_ms=args.metrics_settle_ms,
                question=args.question,
            )
            rows.append(row_from_result(value, result))
    finally:
        try:
            if await runtime_value(http_base, token) != original:
                await apply_value(http_base, token, original)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "values_chunks": values,
        "original_value_chunks": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "sessions": args.sessions,
        "turns_per_session": args.turns,
        "question": args.question,
        "note": (
            "Measures the production LLM->streaming-TTS path. "
            "Only the internal bounded VieNeu producer queue changes; "
            "device send-ahead/pacing remains unchanged."
        ),
        "rows": rows,
    }
    out_dir = Path(args.artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"tts-buffer-matrix-{stamp}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["artifact_path"] = str(path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--health", default="http://127.0.0.1:8003/health")
    parser.add_argument("--values", default="4,16,64")
    parser.add_argument("--sessions", type=int, default=2)
    parser.add_argument("--turns", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--turn-cooldown-ms", type=int, default=300)
    parser.add_argument("--variant-cooldown-ms", type=int, default=750)
    parser.add_argument("--metrics-settle-ms", type=int, default=500)
    parser.add_argument("--question", default=DEFAULT_LONG_QUESTION)
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    args = parser.parse_args()
    if (
        args.sessions < 1
        or args.turns < 1
        or args.timeout <= 0
        or args.turn_cooldown_ms < 0
        or args.variant_cooldown_ms < 0
        or args.metrics_settle_ms < 0
    ):
        parser.error("sessions/turns/timeout/cooldown values are invalid")
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("tts-buffer-matrix"):
            report = asyncio.run(run_matrix(args))
    except MatrixError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("restored") else 1


if __name__ == "__main__":
    raise SystemExit(main())
