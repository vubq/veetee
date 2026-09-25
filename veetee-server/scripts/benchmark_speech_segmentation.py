#!/usr/bin/env python3
"""A/B LLM-to-TTS speech segmentation without hard-coding a winner.

The runner hot-applies one speech-segmentation threshold, executes the same
stock Xiaozhi labelled voice benchmark for every variant, and restores the
effective value observed before the matrix.

Latency is descriptive only. Production selection still requires listening /
prosody quality acceptance; this script never auto-selects a "best" value.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark_pipeline
    from scripts.benchmark_endpoint_matrix import (
        MatrixError,
        active_session_count,
        benchmark_run_lock,
        apply_values,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        server_latency_summary,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )
except ModuleNotFoundError:  # direct execution: python scripts/...
    import benchmark_pipeline
    from benchmark_endpoint_matrix import (
        MatrixError,
        active_session_count,
        benchmark_run_lock,
        apply_values,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        server_latency_summary,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )


SUPPORTED_SETTINGS = {
    "llm.speech_segmentation.min_segment_chars": (4, 500),
    "llm.speech_segmentation.clause_target_chars": (16, 1000),
    "llm.speech_segmentation.clause_min_chars": (8, 1000),
    "llm.speech_segmentation.first_clause_min_chars": (4, 500),
    "llm.speech_segmentation.first_clause_min_words": (1, 20),
    "llm.speech_segmentation.hard_max_segment_chars": (32, 4000),
    "llm.speech_segmentation.hard_cut_search_back": (1, 1000),
    "llm.speech_segmentation.hard_cut_search_forward": (1, 500),
    "llm.speech_segmentation.first_segment_min_chars": (2, 200),
    "llm.speech_segmentation.first_segment_min_words": (1, 20),
    "llm.speech_segmentation.first_soft_cut_chars": (0, 200),
    "llm.speech_segmentation.first_soft_cut_min_words": (1, 20),
}


def parse_values(raw: str, *, setting: str) -> list[int]:
    if setting not in SUPPORTED_SETTINGS:
        raise MatrixError(f"unsupported speech segmentation setting: {setting}")
    minimum, maximum = SUPPORTED_SETTINGS[setting]
    values: list[int] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise MatrixError(f"invalid speech segmentation value: {part!r}") from exc
        if not minimum <= value <= maximum:
            raise MatrixError(
                f"{setting} value {value} outside supported range "
                f"{minimum}..{maximum}"
            )
        if value not in values:
            values.append(value)
    if not values:
        raise MatrixError("speech segmentation matrix is empty")
    return values


def effective_value(diagnostics: dict[str, Any], setting: str) -> int:
    field = setting.rsplit(".", 1)[1]
    value = diagnostics.get("profile", {}).get("speech_segmentation", {}).get(field)
    if isinstance(value, bool):
        raise MatrixError(f"diagnostics missing effective {setting}")
    try:
        return int(value)
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
        turn_cooldown_ms=args.turn_cooldown_ms,
    )


async def apply_setting(http_base: str, token: str, setting: str, value: int) -> dict[str, Any]:
    return await apply_values(
        http_base,
        token,
        {setting: int(value)},
    )


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    token = load_management_token(args)
    values = parse_values(args.values, setting=args.setting)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=args.startup_timeout)
    await ensure_benchmark_device_token(http_base, token)
    diagnostics = await request_json(
        "GET",
        f"{http_base}/api/diagnostics",
        token=token,
    )
    baseline_active_sessions = active_session_count(diagnostics)
    original = effective_value(diagnostics, args.setting)
    asr_profile = diagnostics.get("profile", {}).get("asr", {})
    original_asr = {
        "asr.min_silence_duration_ms": int(
            asr_profile.get("min_silence_duration_ms", 450)
        ),
        "asr.speculative_inference_enabled": bool(
            asr_profile.get("speculative_inference_enabled", False)
        ),
        "asr.speculative_start_silence_ms": int(
            asr_profile.get("speculative_start_silence_ms", 64)
        ),
        "asr.speculative_min_confidence": float(
            asr_profile.get("speculative_min_confidence", 0.95)
        ),
    }
    asr_override: dict[str, Any] = {}
    if args.asr_min_silence_ms is not None:
        asr_override["asr.min_silence_duration_ms"] = args.asr_min_silence_ms
    if args.speculative != "keep":
        asr_override["asr.speculative_inference_enabled"] = args.speculative == "on"
        asr_override["asr.speculative_start_silence_ms"] = (
            args.speculative_start_silence_ms
        )
        asr_override["asr.speculative_min_confidence"] = (
            args.speculative_min_confidence
        )
    effective_min_silence = int(
        asr_override.get(
            "asr.min_silence_duration_ms",
            original_asr["asr.min_silence_duration_ms"],
        )
    )
    if (
        asr_override.get("asr.speculative_inference_enabled") is True
        and args.speculative_start_silence_ms >= effective_min_silence
    ):
        raise MatrixError(
            "speculative start silence must be lower than effective ASR min silence"
        )
    if asr_override:
        await wait_for_quiescent_sessions(
            http_base,
            token,
            timeout_seconds=args.quiesce_timeout,
            max_active_sessions=baseline_active_sessions,
        )
        applied_asr = await apply_values(http_base, token, asr_override)
        if not applied_asr.get("applied", False):
            raise MatrixError("runtime did not confirm ASR benchmark override")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"speech-segmentation-matrix-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""
    try:
        for index, value in enumerate(values):
            await wait_for_quiescent_sessions(
                http_base,
                token,
                timeout_seconds=args.quiesce_timeout,
                max_active_sessions=baseline_active_sessions,
            )
            if index > 0 and args.variant_cooldown_ms > 0:
                await asyncio.sleep(args.variant_cooldown_ms / 1000.0)
            applied = await apply_setting(http_base, token, args.setting, value)
            if not applied.get("applied", False):
                raise MatrixError(
                    f"runtime did not confirm applying {args.setting}={value}"
                )
            variant_dir = root / f"{args.setting.replace('.', '-')}-{value}"
            variant_dir.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                benchmark_args(args, variant_dir)
            )
            diagnostics_after = await request_json(
                "GET",
                f"{http_base}/api/diagnostics",
                token=token,
            )
            server_latency_ms = server_latency_summary(
                diagnostics_after,
                records,
            )
            summary.update({
                "speech_segmentation_setting": args.setting,
                "speech_segmentation_value": value,
                "prosody_quality_not_measured": True,
                "server_latency_ms": server_latency_ms,
            })
            with (variant_dir / "runs.jsonl").open("w", encoding="utf-8") as output:
                for record in records:
                    output.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            (variant_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            rows.append({
                "value": value,
                "requested_samples": summary.get("requested_samples"),
                "successful_samples": summary.get("successful_samples"),
                "success_rate": summary.get("success_rate"),
                "p50_ms": summary.get("p50_ms"),
                "p90_ms": summary.get("p90_ms"),
                "p95_ms": summary.get("p95_ms"),
                "max_ms": summary.get("max_ms"),
                "sla_status": summary.get("sla_status"),
                "stage_latency_ms": summary.get("stage_latency_ms", {}),
                "server_latency_ms": server_latency_ms,
            })
            await wait_for_quiescent_sessions(
                http_base,
                token,
                timeout_seconds=args.quiesce_timeout,
                max_active_sessions=baseline_active_sessions,
            )
    finally:
        try:
            try:
                await wait_for_quiescent_sessions(
                    http_base,
                    token,
                    timeout_seconds=args.quiesce_timeout,
                    max_active_sessions=baseline_active_sessions,
                )
            except Exception:
                pass
            restore_values: dict[str, Any] = {args.setting: original}
            if asr_override:
                restore_values.update(original_asr)
            await apply_values(http_base, token, restore_values)
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
        "fixture": args.wav,
        "mode": args.mode,
        "runs_per_value": args.runs,
        "warmup_per_value": args.warmup,
        "certification_mode": bool(args.certification),
        "prosody_quality_not_measured": True,
        "asr_override": asr_override or None,
        "selection_policy": (
            "Do not choose a production speech segmentation threshold from "
            "latency alone. Require listening/prosody and answer-quality gates."
        ),
        "rows": rows,
    }
    aggregate_path = root / "matrix.json"
    aggregate_path.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    aggregate["artifact_path"] = str(aggregate_path)
    return aggregate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hot-apply speech segmentation thresholds and benchmark TTFA"
    )
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument(
        "--setting",
        choices=tuple(SUPPORTED_SETTINGS),
        default="llm.speech_segmentation.first_clause_min_chars",
    )
    parser.add_argument("--values", default="36,28,24,18")
    parser.add_argument(
        "--asr-min-silence-ms",
        type=int,
        default=None,
        help="optional ASR silence override held constant during the matrix",
    )
    parser.add_argument(
        "--speculative",
        choices=("keep", "on", "off"),
        default="keep",
        help="optional speculative local-ASR override held constant during the matrix",
    )
    parser.add_argument("--speculative-start-silence-ms", type=int, default=64)
    parser.add_argument("--speculative-min-confidence", type=float, default=0.95)
    parser.add_argument("--mode", choices=("auto", "manual"), default="auto")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--auto-silence-ms", type=int, default=720)
    parser.add_argument(
        "--voiced-rms-threshold",
        type=float,
        default=benchmark_pipeline.DEFAULT_VOICED_RMS_THRESHOLD,
    )
    parser.add_argument("--reconnect-each", action="store_true")
    parser.add_argument(
        "--turn-cooldown-ms",
        type=int,
        default=750,
        help="delay between turns; excluded from per-turn latency",
    )
    parser.add_argument(
        "--variant-cooldown-ms",
        type=int,
        default=1000,
        help="quiet period between segmentation variants",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="wait for the HTTP management plane after a cold service restart",
    )
    parser.add_argument(
        "--quiesce-timeout",
        type=float,
        default=15.0,
        help="wait for active_sessions=0 before hot-applying the next variant",
    )
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--certification", action="store_true")
    parser.add_argument(
        "--management-token-env",
        default="VEETEE_MANAGEMENT_TOKEN",
        help="environment variable containing the management token; value is never printed",
    )
    parser.add_argument(
        "--management-env-file",
        default=".env",
        help="fallback dotenv-style file used only to read the named management token",
    )
    args = parser.parse_args()
    if (
        args.runs < 1
        or args.warmup < 0
        or args.timeout <= 0
        or args.turn_cooldown_ms < 0
        or args.variant_cooldown_ms < 0
        or args.startup_timeout <= 0
        or args.quiesce_timeout <= 0
        or (
            args.asr_min_silence_ms is not None
            and not 96 <= args.asr_min_silence_ms <= 2000
        )
        or not 32 <= args.speculative_start_silence_ms <= 1000
        or not 0 <= args.speculative_min_confidence <= 1
    ):
        parser.error(
            "runs/warmup/timeout/cooldown/quiesce/ASR override values are invalid"
        )
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("speech-segmentation-matrix"):
            result = asyncio.run(run_matrix(args))
    except (MatrixError, benchmark_pipeline.BenchmarkError) as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (
        result.get("restored") and result.get("benchmark_device_revoked")
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
