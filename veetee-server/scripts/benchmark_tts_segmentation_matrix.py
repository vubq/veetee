#!/usr/bin/env python3
"""A/B bounded speech segmentation under concurrent production TTS load.

The matrix changes only transport/prosody thresholds. It never inspects user
keywords or semantic intent. Every profile is restored to the original runtime
configuration in a finally block.
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
except (ModuleNotFoundError, SyntaxError):
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

PROFILE_FIELDS = (
    "clause_target_chars",
    "clause_min_chars",
    "hard_max_segment_chars",
)

DEFAULT_PROFILES = {
    "baseline": {
        "clause_target_chars": 150,
        "clause_min_chars": 80,
        "hard_max_segment_chars": 240,
    },
    "balanced": {
        "clause_target_chars": 100,
        "clause_min_chars": 60,
        "hard_max_segment_chars": 160,
    },
    "compact": {
        "clause_target_chars": 80,
        "clause_min_chars": 48,
        "hard_max_segment_chars": 120,
    },
    "fast": {
        "clause_target_chars": 64,
        "clause_min_chars": 40,
        "hard_max_segment_chars": 96,
    },
    "micro": {
        "clause_target_chars": 48,
        "clause_min_chars": 32,
        "hard_max_segment_chars": 64,
    },
    "tiny": {
        "clause_target_chars": 40,
        "clause_min_chars": 28,
        "hard_max_segment_chars": 56,
    },
}


def runtime_key(field: str) -> str:
    return f"llm.speech_segmentation.{field}"


async def diagnostics(http_base: str, token: str) -> dict[str, Any]:
    return await request_json(
        "GET",
        f"{http_base.rstrip('/')}/api/diagnostics",
        token=token,
    )


def current_policy(snapshot: dict[str, Any]) -> dict[str, int]:
    profile = snapshot.get("profile", {}).get("speech_segmentation", {})
    result: dict[str, int] = {}
    for field in PROFILE_FIELDS:
        try:
            result[field] = int(profile[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise MatrixError(f"diagnostics missing speech segmentation {field}") from exc
    return result


def validate_profile(profile: dict[str, int]) -> None:
    target = int(profile["clause_target_chars"])
    minimum = int(profile["clause_min_chars"])
    hard_max = int(profile["hard_max_segment_chars"])
    if not 16 <= target <= 1000:
        raise MatrixError("clause_target_chars outside supported range")
    if not 8 <= minimum <= target:
        raise MatrixError("clause_min_chars must be 8..clause_target_chars")
    if not 32 <= hard_max <= 4000 or hard_max < target:
        raise MatrixError("hard_max_segment_chars must be >= clause_target_chars")


async def apply_profile(
    http_base: str,
    token: str,
    profile: dict[str, int],
) -> None:
    validate_profile(profile)
    values = {runtime_key(field): int(value) for field, value in profile.items()}
    result = await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={"values": values},
        timeout=30.0,
    )
    if not result.get("applied", False):
        raise MatrixError("runtime did not confirm speech segmentation profile")


def row_from_result(name: str, profile: dict[str, int], result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("server_metrics") or {}
    return {
        "profile": name,
        "values": profile,
        "success_rate": result.get("success_rate"),
        "protocol_success_rate": result.get("protocol_success_rate"),
        "trace_complete": result.get("trace_complete"),
        "turn_start_to_first_ws_binary_ms": metrics.get("turn_start_to_first_ws_binary_ms"),
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
    original = current_policy(await diagnostics(http_base, token))

    names = [name.strip() for name in args.profiles.split(",") if name.strip()]
    if not names:
        raise MatrixError("speech segmentation profile matrix is empty")
    for name in names:
        if name not in DEFAULT_PROFILES:
            raise MatrixError(f"unknown speech segmentation profile: {name}")

    rows: list[dict[str, Any]] = []
    restore_error = ""
    try:
        for index, name in enumerate(names):
            profile = DEFAULT_PROFILES[name]
            await apply_profile(http_base, token, profile)
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
            rows.append(row_from_result(name, profile, result))
    finally:
        try:
            await apply_profile(http_base, token, original)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "original": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "sessions": args.sessions,
        "turns_per_session": args.turns,
        "question": args.question,
        "profiles": names,
        "note": (
            "Only transport/prosody segmentation thresholds change. "
            "Use concurrency metrics to reduce TTS engine hold time, then run "
            "audio continuity/corpus acceptance before promoting a profile."
        ),
        "rows": rows,
    }
    out_dir = Path(args.artifact_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"tts-segmentation-matrix-{stamp}.json"
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
    parser.add_argument(
        "--profiles",
        default="baseline,balanced,compact,fast",
    )
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
        with benchmark_run_lock("tts-segmentation-matrix"):
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
