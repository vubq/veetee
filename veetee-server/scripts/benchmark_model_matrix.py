#!/usr/bin/env python3
"""A/B configured LLM models on the same labelled voice fixture.

The runner uses the normal runtime hot-swap path, benchmark device pairing and
voice pipeline. It always restores the original model and revokes any
ephemeral benchmark device. Latency is descriptive; a production model change
still requires semantic/persona/tool acceptance.
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
        wait_for_quiescent_sessions,
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
        wait_for_quiescent_sessions,
    )


def parse_models(raw: str) -> list[str]:
    models: list[str] = []
    for part in str(raw or "").split(","):
        model = part.strip()
        if model and model not in models:
            models.append(model)
    if not models:
        raise MatrixError("model matrix is empty")
    return models


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


async def apply_model(http_base: str, token: str, model: str) -> dict[str, Any]:
    return await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={"values": {"llm.model": model}},
        timeout=30.0,
    )


def effective_model(diagnostics: dict[str, Any]) -> str:
    model = str(
        (diagnostics.get("llm") or {}).get("model")
        or (diagnostics.get("profile") or {}).get("llm", {}).get("model")
        or ""
    ).strip()
    if not model:
        raise MatrixError("diagnostics missing effective llm.model")
    return model


async def run_matrix(args: argparse.Namespace) -> dict[str, Any]:
    token = load_management_token(args)
    models = parse_models(args.models)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=args.startup_timeout)
    await ensure_benchmark_device_token(http_base, token)

    diagnostics = await request_json(
        "GET", f"{http_base}/api/diagnostics", token=token
    )
    original = effective_model(diagnostics)
    advertised = {
        str(item).strip()
        for item in ((diagnostics.get("llm") or {}).get("models") or [])
        if str(item).strip()
    }
    if advertised:
        unknown = [model for model in models if model not in advertised]
        if unknown:
            raise MatrixError(
                "model(s) not advertised by runtime: " + ", ".join(unknown)
            )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"model-matrix-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""
    try:
        for index, model in enumerate(models):
            await wait_for_quiescent_sessions(
                http_base,
                token,
                timeout_seconds=args.quiesce_timeout,
            )
            if index > 0 and args.variant_cooldown_ms > 0:
                await asyncio.sleep(args.variant_cooldown_ms / 1000.0)
            applied = await apply_model(http_base, token, model)
            if not applied.get("applied", False):
                raise MatrixError(f"runtime did not confirm llm.model={model}")
            variant = root / model.replace("/", "__")
            variant.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                benchmark_args(args, variant)
            )
            diagnostics_after = await request_json(
                "GET",
                f"{http_base}/api/diagnostics",
                token=token,
            )
            server_latency_ms = server_latency_summary(diagnostics_after, records)
            summary.update({
                "llm_model": model,
                "semantic_quality_not_measured": True,
                "server_latency_ms": server_latency_ms,
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
                "model": model,
                "requested_samples": summary.get("requested_samples"),
                "successful_samples": summary.get("successful_samples"),
                "success_rate": summary.get("success_rate"),
                "p50_ms": summary.get("p50_ms"),
                "p95_ms": summary.get("p95_ms"),
                "stage_latency_ms": summary.get("stage_latency_ms", {}),
                "server_latency_ms": server_latency_ms,
                "sla_status": summary.get("sla_status"),
            })
            await wait_for_quiescent_sessions(
                http_base,
                token,
                timeout_seconds=args.quiesce_timeout,
            )
    finally:
        try:
            try:
                await wait_for_quiescent_sessions(
                    http_base,
                    token,
                    timeout_seconds=args.quiesce_timeout,
                )
            except Exception:
                pass
            await apply_model(http_base, token, original)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"
        cleanup_error = await cleanup_benchmark_device(http_base, token)

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "original_model": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "benchmark_device_revoked": not cleanup_error,
        "benchmark_device_cleanup_error": cleanup_error or None,
        "semantic_quality_not_measured": True,
        "selection_policy": (
            "Do not change the production model from latency alone. Require "
            "semantic/persona/tool acceptance on the same source snapshot."
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
    parser = argparse.ArgumentParser(description="A/B configured LLM models")
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument("--models", required=True)
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
    parser.add_argument(
        "--turn-cooldown-ms",
        type=int,
        default=1500,
        help="delay between turns; excluded from per-turn latency",
    )
    parser.add_argument(
        "--variant-cooldown-ms",
        type=int,
        default=1500,
        help="quiet period between model variants",
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
        help="wait for active_sessions=0 before swapping models",
    )
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--certification", action="store_true")
    parser.add_argument(
        "--management-token-env",
        default="VEETEE_MANAGEMENT_TOKEN",
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
    ):
        parser.error("runs/warmup/timeout/cooldown/quiesce values are invalid")
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("model-matrix"):
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
