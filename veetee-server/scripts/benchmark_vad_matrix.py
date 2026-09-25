#!/usr/bin/env python3
"""A/B Silero/Parakeet VAD policy without changing production defaults.

Each variant is hot-applied through the normal runtime transaction, measured
with the stock Xiaozhi voice benchmark, enriched with retained server turn
metrics, then restored. Latency never selects a winner by itself.
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
        server_latency_summary,
        wait_for_http_ready,
    )
except ModuleNotFoundError:  # direct execution: python scripts/...
    import benchmark_pipeline
    from benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        server_latency_summary,
        wait_for_http_ready,
    )


SETTINGS = {
    "asr.vad_end_threshold": (0.01, 0.99, float),
    "asr.vad_threshold": (0.05, 0.99, float),
    "asr.vad_threshold_low": (0.01, 0.95, float),
    "asr.pre_speech_pad_ms": (0, 2000, int),
    "asr.speech_start_frames": (1, 8, int),
    "asr.min_speech_duration_ms": (64, 2000, int),
}


def parse_values(raw: str, *, setting: str) -> list[int | float]:
    minimum, maximum, cast = SETTINGS[setting]
    values: list[int | float] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if cast is int:
                numeric = float(part)
                if not numeric.is_integer():
                    raise ValueError
                value: int | float = int(numeric)
            else:
                value = float(part)
        except ValueError as exc:
            raise MatrixError(f"invalid {setting} value: {part!r}") from exc
        if not minimum <= value <= maximum:
            raise MatrixError(
                f"{setting} value {value} outside supported range "
                f"{minimum}..{maximum}"
            )
        if value not in values:
            values.append(value)
    if not values:
        raise MatrixError("VAD matrix is empty")
    return values


def effective_value(diagnostics: dict[str, Any], setting: str) -> int | float:
    field = setting.split(".", 1)[1]
    raw = diagnostics.get("profile", {}).get("asr", {}).get(field)
    if isinstance(raw, bool) or raw is None:
        raise MatrixError(f"diagnostics missing effective {setting}")
    _minimum, _maximum, cast = SETTINGS[setting]
    try:
        return cast(raw)
    except (TypeError, ValueError) as exc:
        raise MatrixError(f"diagnostics missing effective {setting}") from exc


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
    )


async def apply_setting(
    http_base: str,
    token: str,
    setting: str,
    value: int | float,
) -> dict[str, Any]:
    return await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={"values": {setting: value}},
        timeout=30.0,
    )


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    token = load_management_token(args)
    values = parse_values(args.values, setting=args.setting)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=args.startup_timeout)
    await ensure_benchmark_device_token(http_base, token)
    diagnostics = await request_json(
        "GET", f"{http_base}/api/diagnostics", token=token
    )
    original = effective_value(diagnostics, args.setting)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"vad-matrix-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""
    try:
        for value in values:
            applied = await apply_setting(http_base, token, args.setting, value)
            if not applied.get("applied", False):
                raise MatrixError(
                    f"runtime did not confirm applying {args.setting}={value}"
                )
            variant = root / f"{args.setting.replace('.', '-')}-{value}"
            variant.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                benchmark_args(args, variant)
            )
            diagnostics_after = await request_json(
                "GET", f"{http_base}/api/diagnostics", token=token
            )
            server_latency_ms = server_latency_summary(
                diagnostics_after, records
            )
            summary.update({
                "vad_setting": args.setting,
                "vad_value": value,
                "server_latency_ms": server_latency_ms,
                "quality_not_measured": True,
            })
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
            rows.append({
                "value": value,
                "requested_samples": summary.get("requested_samples"),
                "successful_samples": summary.get("successful_samples"),
                "success_rate": summary.get("success_rate"),
                "p50_ms": summary.get("p50_ms"),
                "p95_ms": summary.get("p95_ms"),
                "stage_latency_ms": summary.get("stage_latency_ms", {}),
                "server_latency_ms": server_latency_ms,
                "sla_status": summary.get("sla_status"),
            })
    finally:
        try:
            await apply_setting(http_base, token, args.setting, original)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"
        cleanup_error = await cleanup_benchmark_device(http_base, token)

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "setting": args.setting,
        "values": values,
        "original_value": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "benchmark_device_revoked": not cleanup_error,
        "benchmark_device_cleanup_error": cleanup_error or None,
        "quality_not_measured": True,
        "selection_policy": (
            "Do not promote a VAD policy from latency alone. Require labelled "
            "false-cut and WER/CER acceptance on noisy, hesitant and short speech."
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
    parser = argparse.ArgumentParser(description="A/B hot VAD policy settings")
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument("--setting", choices=tuple(SETTINGS), default="asr.vad_end_threshold")
    parser.add_argument("--values", default="0.3,0.6,0.8,0.95")
    parser.add_argument("--mode", choices=("auto", "manual"), default="auto")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--auto-silence-ms", type=int, default=720)
    parser.add_argument(
        "--voiced-rms-threshold",
        type=float,
        default=benchmark_pipeline.DEFAULT_VOICED_RMS_THRESHOLD,
    )
    parser.add_argument("--reconnect-each", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--certification", action="store_true")
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("vad-matrix"):
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
