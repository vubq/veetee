#!/usr/bin/env python3
"""Benchmark one realtime profile across a labelled Vietnamese audio corpus.

Fixture labels live beside WAV files as <name>.wav.json:
{
  "speech_end_sample": 12345,
  "expected_transcript": "..."
}

This runner never selects a production winner. It applies one requested
runtime profile, benchmarks all labelled fixtures, writes raw artifacts, then
restores the effective runtime profile and revokes its temporary benchmark
device.
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
        active_session_count,
        apply_values,
        benchmark_run_lock,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        runtime_restore_mismatches,
        server_latency_summary,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )
except ModuleNotFoundError:  # direct execution: python scripts/...
    import benchmark_pipeline
    from benchmark_endpoint_matrix import (
        MatrixError,
        active_session_count,
        apply_values,
        benchmark_run_lock,
        cleanup_benchmark_device,
        ensure_benchmark_device_token,
        load_management_token,
        request_json,
        runtime_restore_mismatches,
        server_latency_summary,
        wait_for_http_ready,
        wait_for_quiescent_sessions,
    )


PROFILE_KEYS = (
    "asr.min_silence_duration_ms",
    "asr.vad_end_threshold",
    "asr.speculative_inference_enabled",
    "asr.speculative_start_silence_ms",
    "asr.speculative_min_confidence",
    "asr.speculative_llm_enabled",
    "asr.speculative_llm_min_confidence",
    "tts.speculative_prefetch_enabled",
    "llm.speech_segmentation.first_soft_cut_chars",
    "llm.speech_segmentation.first_soft_cut_min_words",
)


def discover_fixtures(root: Path) -> list[Path]:
    fixtures: list[Path] = []
    for wav in sorted(root.glob("*.wav")):
        metadata_path = wav.with_suffix(wav.suffix + ".json")
        if not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(metadata, dict):
            continue
        if metadata.get("speech_end_sample") is None:
            continue
        if not str(metadata.get("expected_transcript") or "").strip():
            continue
        fixtures.append(wav)
    return fixtures


def _profile_snapshot(diagnostics: dict[str, Any]) -> dict[str, Any]:
    profile = diagnostics.get("profile", {})
    asr = profile.get("asr", {})
    tts = profile.get("tts", {})
    speech = profile.get("speech_segmentation", {})
    return {
        "asr.min_silence_duration_ms": int(asr.get("min_silence_duration_ms", 450)),
        "asr.vad_end_threshold": float(
            asr.get("vad_end_threshold", asr.get("vad_threshold_low", 0.3))
        ),
        "asr.speculative_inference_enabled": bool(
            asr.get("speculative_inference_enabled", False)
        ),
        "asr.speculative_start_silence_ms": int(
            asr.get("speculative_start_silence_ms", 64)
        ),
        "asr.speculative_min_confidence": float(
            asr.get("speculative_min_confidence", 0.95)
        ),
        "asr.speculative_llm_enabled": bool(
            asr.get("speculative_llm_enabled", False)
        ),
        "asr.speculative_llm_min_confidence": float(
            asr.get("speculative_llm_min_confidence", 0.95)
        ),
        "tts.speculative_prefetch_enabled": bool(
            tts.get("speculative_prefetch_enabled", False)
        ),
        "llm.speech_segmentation.first_soft_cut_chars": int(
            speech.get("first_soft_cut_chars", 0)
        ),
        "llm.speech_segmentation.first_soft_cut_min_words": int(
            speech.get("first_soft_cut_min_words", 3)
        ),
    }


def requested_profile(args: argparse.Namespace, original: dict[str, Any]) -> dict[str, Any]:
    profile = dict(original)
    if args.min_silence_ms is not None:
        profile["asr.min_silence_duration_ms"] = int(args.min_silence_ms)
    vad_end_threshold = getattr(args, "vad_end_threshold", None)
    if vad_end_threshold is not None:
        profile["asr.vad_end_threshold"] = float(vad_end_threshold)
    if args.speculative != "keep":
        profile["asr.speculative_inference_enabled"] = args.speculative == "on"
    if args.speculative_start_silence_ms is not None:
        profile["asr.speculative_start_silence_ms"] = int(
            args.speculative_start_silence_ms
        )
    if args.speculative_min_confidence is not None:
        profile["asr.speculative_min_confidence"] = float(
            args.speculative_min_confidence
        )
    if args.speculative_llm != "keep":
        profile["asr.speculative_llm_enabled"] = args.speculative_llm == "on"
    if args.speculative_llm_min_confidence is not None:
        profile["asr.speculative_llm_min_confidence"] = float(
            args.speculative_llm_min_confidence
        )
    if args.speculative_tts != "keep":
        profile["tts.speculative_prefetch_enabled"] = args.speculative_tts == "on"
    if args.first_soft_cut_chars is not None:
        profile["llm.speech_segmentation.first_soft_cut_chars"] = int(
            args.first_soft_cut_chars
        )
    if args.first_soft_cut_min_words is not None:
        profile["llm.speech_segmentation.first_soft_cut_min_words"] = int(
            args.first_soft_cut_min_words
        )
    if (
        profile["asr.speculative_inference_enabled"]
        and profile["asr.speculative_start_silence_ms"]
        >= profile["asr.min_silence_duration_ms"]
    ):
        raise MatrixError(
            "speculative_start_silence_ms must be lower than min_silence_duration_ms"
        )
    if (
        profile["asr.speculative_llm_enabled"]
        and not profile["asr.speculative_inference_enabled"]
    ):
        raise MatrixError(
            "speculative LLM requires speculative local ASR"
        )
    return profile


def pipeline_args(
    args: argparse.Namespace,
    wav: Path,
    artifact_dir: Path,
) -> Namespace:
    return Namespace(
        uri=args.uri,
        wav=str(wav),
        speech_end_sample=None,
        expected_transcript=None,
        mode=args.mode,
        runs=args.runs,
        warmup=args.warmup,
        timeout=args.timeout,
        auto_silence_ms=args.auto_silence_ms,
        voiced_rms_threshold=args.voiced_rms_threshold,
        reconnect_each=True,
        turn_cooldown_ms=args.turn_cooldown_ms,
        artifact_dir=str(artifact_dir),
        certification=args.certification,
    )


def corpus_coverage(fixtures: list[Path]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    environments: dict[str, int] = {}
    critical = 0
    for wav in fixtures:
        try:
            metadata = json.loads(
                wav.with_suffix(wav.suffix + ".json").read_text(encoding="utf-8")
            )
        except Exception:
            metadata = {}
        category = str(metadata.get("category") or "unlabelled").strip() or "unlabelled"
        environment = str(metadata.get("environment") or "unspecified").strip() or "unspecified"
        categories[category] = categories.get(category, 0) + 1
        environments[environment] = environments.get(environment, 0) + 1
        if metadata.get("critical"):
            critical += 1
    return {
        "fixtures": len(fixtures),
        "critical": critical,
        "categories": dict(sorted(categories.items())),
        "environments": dict(sorted(environments.items())),
    }


def representative_corpus_errors(
    coverage: dict[str, Any],
    *,
    min_fixtures: int,
) -> list[str]:
    errors: list[str] = []
    fixtures = int(coverage.get("fixtures") or 0)
    if fixtures < min_fixtures:
        errors.append(f"fixtures {fixtures} < required {min_fixtures}")
    categories = coverage.get("categories") or {}
    required = {
        "very_short": 3,
        "internal_pause": 3,
        "hesitation": 3,
        "proper_name": 3,
        "long_number": 3,
        "long_sentence": 3,
        "background_noise": 3,
        "negation": 3,
    }
    for category, minimum in required.items():
        actual = int(categories.get(category) or 0)
        if actual < minimum:
            errors.append(f"category {category}: {actual} < {minimum}")
    return errors


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    requested = sum(int(row.get("requested_samples") or 0) for row in rows)
    successful = sum(int(row.get("successful_samples") or 0) for row in rows)
    labelled = sum(
        int((row.get("quality") or {}).get("labelled_samples") or 0)
        for row in rows
    )
    splits = sum(
        int((row.get("quality") or {}).get("split_or_missing_final_samples") or 0)
        for row in rows
    )
    wers = [
        float((row.get("quality") or {})["wer_mean"])
        for row in rows
        if (row.get("quality") or {}).get("wer_mean") is not None
    ]
    cers = [
        float((row.get("quality") or {})["cer_mean"])
        for row in rows
        if (row.get("quality") or {}).get("cer_mean") is not None
    ]
    latencies = [
        float(row["p50_ms"])
        for row in rows
        if row.get("p50_ms") is not None
    ]
    return {
        "fixture_count": len(rows),
        "requested_samples": requested,
        "successful_samples": successful,
        "success_rate": round(successful / requested, 4) if requested else 0.0,
        "labelled_samples": labelled,
        "split_or_missing_final_samples": splits,
        "mean_fixture_wer": round(sum(wers) / len(wers), 6) if wers else None,
        "max_fixture_wer": round(max(wers), 6) if wers else None,
        "mean_fixture_cer": round(sum(cers) / len(cers), 6) if cers else None,
        "max_fixture_cer": round(max(cers), 6) if cers else None,
        "mean_fixture_p50_ms": (
            round(sum(latencies) / len(latencies), 3) if latencies else None
        ),
        "max_fixture_p50_ms": round(max(latencies), 3) if latencies else None,
    }


async def run_corpus(args: argparse.Namespace) -> dict[str, Any]:
    fixtures = discover_fixtures(Path(args.corpus_dir))
    if not fixtures:
        raise MatrixError(
            f"no labelled WAV fixtures found in {args.corpus_dir!r}"
        )
    coverage = corpus_coverage(fixtures)
    representative_errors = representative_corpus_errors(
        coverage,
        min_fixtures=args.min_representative_fixtures,
    )
    if args.require_representative_corpus and representative_errors:
        raise MatrixError(
            "corpus is not representative: " + "; ".join(representative_errors)
        )

    token = load_management_token(args)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=args.http_ready_timeout)
    await ensure_benchmark_device_token(http_base, token)
    diagnostics = await request_json(
        "GET", f"{http_base}/api/diagnostics", token=token
    )
    baseline_active_sessions = active_session_count(diagnostics)
    original = _profile_snapshot(diagnostics)
    profile = requested_profile(args, original)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"corpus-{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""
    try:
        await wait_for_quiescent_sessions(
            http_base,
            token,
            timeout_seconds=args.quiesce_timeout,
            max_active_sessions=baseline_active_sessions,
        )
        applied = await apply_values(http_base, token, profile)
        if not applied.get("applied", False):
            raise MatrixError("runtime did not confirm corpus profile apply")
        if args.profile_settle_ms > 0:
            await asyncio.sleep(args.profile_settle_ms / 1000.0)

        for index, wav in enumerate(fixtures):
            if index > 0 and args.fixture_cooldown_ms > 0:
                await asyncio.sleep(args.fixture_cooldown_ms / 1000.0)
            await wait_for_quiescent_sessions(
                http_base,
                token,
                timeout_seconds=args.quiesce_timeout,
                max_active_sessions=baseline_active_sessions,
            )
            fixture_dir = root / wav.stem
            fixture_dir.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                pipeline_args(args, wav, fixture_dir)
            )
            diagnostics_after = await request_json(
                "GET", f"{http_base}/api/diagnostics", token=token
            )
            server_latency = server_latency_summary(diagnostics_after, records)
            summary["server_latency_ms"] = server_latency
            (fixture_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with (fixture_dir / "runs.jsonl").open("w", encoding="utf-8") as out:
                for record in records:
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
            rows.append({
                "fixture": str(wav),
                "requested_samples": summary.get("requested_samples"),
                "successful_samples": summary.get("successful_samples"),
                "success_rate": summary.get("success_rate"),
                "p50_ms": summary.get("p50_ms"),
                "p95_ms": summary.get("p95_ms"),
                "quality": summary.get("quality", {}),
                "audio_continuity": summary.get("audio_continuity", {}),
                "server_latency_ms": server_latency,
            })
    finally:
        try:
            await wait_for_http_ready(
                http_base,
                timeout_seconds=args.http_ready_timeout,
            )
            try:
                await wait_for_quiescent_sessions(
                    http_base,
                    token,
                    timeout_seconds=args.quiesce_timeout,
                    max_active_sessions=baseline_active_sessions,
                )
            except Exception:
                pass
            await apply_values(http_base, token, original)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {exc}"
        try:
            restored_diagnostics = await request_json(
                "GET", f"{http_base}/api/diagnostics", token=token
            )
            mismatches = runtime_restore_mismatches(
                restored_diagnostics,
                original,
            )
            if mismatches:
                detail = json.dumps(
                    mismatches,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                restore_error = (
                    f"effective runtime restore mismatch: {detail}"
                    if not restore_error
                    else f"{restore_error}; effective runtime restore mismatch: {detail}"
                )
        except Exception as exc:
            verification_error = f"{type(exc).__name__}: {exc}"
            restore_error = (
                f"restore verification failed: {verification_error}"
                if not restore_error
                else f"{restore_error}; restore verification failed: {verification_error}"
            )
        try:
            await wait_for_http_ready(
                http_base,
                timeout_seconds=args.http_ready_timeout,
            )
            cleanup_error = await cleanup_benchmark_device(http_base, token)
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_dir": args.corpus_dir,
        "profile": profile,
        "original_profile": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "benchmark_device_revoked": not cleanup_error,
        "benchmark_device_cleanup_error": cleanup_error or None,
        "certification_mode": bool(args.certification),
        "coverage": coverage,
        "representative_corpus": not representative_errors,
        "representative_corpus_errors": representative_errors,
        "aggregate": aggregate_rows(rows),
        "fixtures": rows,
        "selection_policy": (
            "Corpus evidence informs tuning but does not auto-promote a runtime "
            "profile. Production promotion requires representative speech/noise "
            "coverage and acceptable split/WER/CER."
        ),
    }
    path = root / "corpus-summary.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result["artifact_path"] = str(path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark one VeeTee ASR profile over labelled audio fixtures"
    )
    parser.add_argument("--corpus-dir", default="eval/audio")
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--min-silence-ms", type=int, default=None)
    parser.add_argument(
        "--vad-end-threshold",
        type=float,
        default=None,
        help="optional Silero end-of-speech threshold override for this corpus run",
    )
    parser.add_argument(
        "--speculative", choices=("keep", "on", "off"), default="keep"
    )
    parser.add_argument("--speculative-start-silence-ms", type=int, default=None)
    parser.add_argument("--speculative-min-confidence", type=float, default=None)
    parser.add_argument(
        "--speculative-llm",
        choices=("keep", "on", "off"),
        default="keep",
    )
    parser.add_argument("--speculative-llm-min-confidence", type=float, default=None)
    parser.add_argument(
        "--speculative-tts",
        choices=("keep", "on", "off"),
        default="keep",
        help="optionally hot-override local speculative TTS prefetch",
    )
    parser.add_argument(
        "--first-soft-cut-chars",
        type=int,
        default=None,
        help="optional first spoken-unit whitespace cut; 0 disables it",
    )
    parser.add_argument(
        "--first-soft-cut-min-words",
        type=int,
        default=None,
        help="minimum words required before first soft cut",
    )
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
    parser.add_argument("--turn-cooldown-ms", type=int, default=1000)
    parser.add_argument("--fixture-cooldown-ms", type=int, default=1500)
    parser.add_argument("--profile-settle-ms", type=int, default=500)
    parser.add_argument("--quiesce-timeout", type=float, default=15.0)
    parser.add_argument("--http-ready-timeout", type=float, default=60.0)
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument(
        "--require-representative-corpus",
        action="store_true",
        help="block runs that do not meet the natural-audio coverage gate",
    )
    parser.add_argument(
        "--min-representative-fixtures",
        type=int,
        default=30,
    )
    parser.add_argument("--certification", action="store_true")
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    args = parser.parse_args()
    if (
        args.runs < 1
        or args.warmup < 0
        or args.timeout <= 0
        or args.min_representative_fixtures < 1
    ):
        parser.error("runs/warmup/timeout/min-representative-fixtures are invalid")
    if args.min_silence_ms is not None and not 96 <= args.min_silence_ms <= 2000:
        parser.error("min-silence-ms must be between 96 and 2000")
    if (
        args.speculative_start_silence_ms is not None
        and not 32 <= args.speculative_start_silence_ms <= 1000
    ):
        parser.error("speculative-start-silence-ms must be between 32 and 1000")
    if (
        args.speculative_min_confidence is not None
        and not 0 <= args.speculative_min_confidence <= 1
    ):
        parser.error("speculative-min-confidence must be between 0 and 1")
    if (
        args.speculative_llm_min_confidence is not None
        and not 0 <= args.speculative_llm_min_confidence <= 1
    ):
        parser.error("speculative-llm-min-confidence must be between 0 and 1")
    if args.first_soft_cut_chars is not None and not 0 <= args.first_soft_cut_chars <= 200:
        parser.error("first-soft-cut-chars must be between 0 and 200")
    if (
        args.first_soft_cut_min_words is not None
        and not 1 <= args.first_soft_cut_min_words <= 20
    ):
        parser.error("first-soft-cut-min-words must be between 1 and 20")
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("corpus"):
            result = asyncio.run(run_corpus(args))
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
