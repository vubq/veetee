#!/usr/bin/env python3
"""A/B speculative local ASR on the same labelled voice fixture.

This runner never changes semantic routing. It toggles only whether the same
Parakeet inference is allowed to start during trailing silence, keeps the
confidence gate explicit, and restores the effective runtime configuration.
Latency is descriptive until endpoint/WER/CER quality gates pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark_pipeline
    from scripts.benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )
except ModuleNotFoundError:
    import benchmark_pipeline
    from benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )


def benchmark_args(args: argparse.Namespace, artifact_dir: Path) -> Namespace:
    return Namespace(
        uri=args.uri,
        wav=args.wav,
        speech_end_sample=args.speech_end_sample,
        mode=args.mode,
        runs=args.runs,
        warmup=args.warmup,
        timeout=args.timeout,
        auto_silence_ms=args.auto_silence_ms,
        voiced_rms_threshold=args.voiced_rms_threshold,
        reconnect_each=args.reconnect_each,
        artifact_dir=str(artifact_dir),
        certification=args.certification,
        turn_cooldown_ms=args.turn_cooldown_ms,
    )


def _profile_asr(diagnostics: dict[str, Any]) -> dict[str, Any]:
    value = diagnostics.get("profile", {}).get("asr", {})
    if not isinstance(value, dict):
        raise MatrixError("diagnostics missing profile.asr")
    return value


async def apply_settings(
    http_base: str,
    token: str,
    *,
    enabled: bool,
    start_silence_ms: int,
    min_confidence: float,
) -> dict[str, Any]:
    return await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={
            "values": {
                "asr.speculative_inference_enabled": bool(enabled),
                "asr.speculative_start_silence_ms": int(start_silence_ms),
                "asr.speculative_min_confidence": float(min_confidence),
            }
        },
    )


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    token = load_management_token(args)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=args.startup_timeout)
    await ensure_benchmark_device_token(http_base, token)
    diagnostics = await request_json(
        "GET", f"{http_base}/api/diagnostics", token=token
    )
    profile = _profile_asr(diagnostics)
    original = {
        "enabled": bool(profile.get("speculative_inference_enabled", False)),
        "start_silence_ms": int(profile.get("speculative_start_silence_ms", 64)),
        "min_confidence": float(profile.get("speculative_min_confidence", 0.95)),
    }
    min_silence_ms = int(profile.get("min_silence_duration_ms", 450))
    if args.start_silence_ms >= min_silence_ms:
        raise MatrixError(
            "speculative start silence must be below effective final endpoint "
            f"({args.start_silence_ms} >= {min_silence_ms})"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"speculative-asr-matrix-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""

    try:
        for index, enabled in enumerate((False, True)):
            await wait_for_quiescent_sessions(
                http_base, token, timeout_seconds=args.quiesce_timeout
            )
            if index and args.variant_cooldown_ms:
                await asyncio.sleep(args.variant_cooldown_ms / 1000.0)
            applied = await apply_settings(
                http_base,
                token,
                enabled=enabled,
                start_silence_ms=args.start_silence_ms,
                min_confidence=args.min_confidence,
            )
            if not applied.get("applied", False):
                raise MatrixError(
                    f"runtime did not confirm speculative ASR enabled={enabled}"
                )
            variant = root / ("enabled" if enabled else "disabled")
            variant.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                benchmark_args(args, variant)
            )
            summary.update(
                {
                    "speculative_inference_enabled": enabled,
                    "speculative_start_silence_ms": args.start_silence_ms,
                    "speculative_min_confidence": args.min_confidence,
                    "quality_not_measured": True,
                }
            )
            with (variant / "runs.jsonl").open("w", encoding="utf-8") as output:
                for record in records:
                    output.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            (variant / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            rows.append(
                {
                    "enabled": enabled,
                    "requested_samples": summary.get("requested_samples"),
                    "successful_samples": summary.get("successful_samples"),
                    "success_rate": summary.get("success_rate"),
                    "p50_ms": summary.get("p50_ms"),
                    "p95_ms": summary.get("p95_ms"),
                    "stage_latency_ms": summary.get("stage_latency_ms", {}),
                    "sla_status": summary.get("sla_status"),
                }
            )
            await wait_for_quiescent_sessions(
                http_base, token, timeout_seconds=args.quiesce_timeout
            )
    finally:
        try:
            try:
                await wait_for_quiescent_sessions(
                    http_base, token, timeout_seconds=args.quiesce_timeout
                )
            except Exception:
                pass
            await apply_settings(
                http_base,
                token,
                enabled=original["enabled"],
                start_silence_ms=original["start_silence_ms"],
                min_confidence=original["min_confidence"],
            )
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"
        cleanup_error = await cleanup_benchmark_device(http_base, token)

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "effective_final_endpoint_ms": min_silence_ms,
        "start_silence_ms": args.start_silence_ms,
        "min_confidence": args.min_confidence,
        "original": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "benchmark_device_revoked": not cleanup_error,
        "benchmark_device_cleanup_error": cleanup_error or None,
        "quality_not_measured": True,
        "selection_policy": (
            "Do not enable speculative ASR in production from latency alone. "
            "Require transcript equality/WER/CER and false-endpoint acceptance."
        ),
        "rows": rows,
    }
    path = root / "matrix.json"
    path.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    aggregate["artifact_path"] = str(path)
    return aggregate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A/B speculative local ASR")
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument("--mode", choices=("auto", "manual"), default="auto")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--auto-silence-ms", type=int, default=720)
    parser.add_argument("--start-silence-ms", type=int, default=64)
    parser.add_argument("--min-confidence", type=float, default=0.95)
    parser.add_argument(
        "--voiced-rms-threshold",
        type=float,
        default=benchmark_pipeline.DEFAULT_VOICED_RMS_THRESHOLD,
    )
    parser.add_argument("--reconnect-each", action="store_true")
    parser.add_argument("--turn-cooldown-ms", type=int, default=1500)
    parser.add_argument("--variant-cooldown-ms", type=int, default=1500)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--quiesce-timeout", type=float, default=15.0)
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--certification", action="store_true")
    parser.add_argument(
        "--management-token-env", default="VEETEE_MANAGEMENT_TOKEN"
    )
    parser.add_argument("--management-env-file", default=".env")
    args = parser.parse_args()
    if (
        args.runs < 1
        or args.warmup < 0
        or args.timeout <= 0
        or args.turn_cooldown_ms < 0
        or args.variant_cooldown_ms < 0
        or args.startup_timeout <= 0
        or args.quiesce_timeout <= 0
        or args.start_silence_ms < 32
        or not 0 <= args.min_confidence <= 1
    ):
        parser.error("invalid benchmark/speculative settings")
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("speculative-asr-matrix"):
            result = asyncio.run(run_matrix(args))
    except (MatrixError, benchmark_pipeline.BenchmarkError) as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("restored") else 1


if __name__ == "__main__":
    raise SystemExit(main())
