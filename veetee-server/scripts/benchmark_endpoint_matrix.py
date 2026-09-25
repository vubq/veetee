#!/usr/bin/env python3
"""A/B endpoint latency without hard-coding a deployment winner.

The runner hot-applies one endpoint setting, executes the existing stock
Xiaozhi voice benchmark with the same labelled WAV fixture, and restores the
effective value observed before the matrix started.

This measures latency/reliability only. It deliberately does not choose a
production endpoint value because false-endpoint/WER/CER quality still needs a
labelled audio corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import benchmark_pipeline
except ModuleNotFoundError:  # direct execution: python scripts/...
    import benchmark_pipeline


SUPPORTED_SETTINGS = {
    "asr.min_silence_duration_ms": (96, 2000),
}


class MatrixError(RuntimeError):
    pass


@contextlib.contextmanager
def benchmark_run_lock(label: str):
    """Prevent concurrent hot-config benchmark matrices from restoring over each other."""
    path = Path(os.getenv("VEETEE_BENCHMARK_LOCK_FILE", "/tmp/veetee-benchmark-matrix.lock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            handle.seek(0)
            holder = handle.read().strip()
            detail = f" ({holder})" if holder else ""
            raise MatrixError(
                f"another VeeTee hot-config benchmark is already running{detail}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} label={label}\n")
        handle.flush()
        yield
    finally:
        if acquired:
            try:
                handle.seek(0)
                handle.truncate()
                handle.flush()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _env_file_value(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def load_management_token(args: argparse.Namespace) -> str:
    token = os.getenv(args.management_token_env, "").strip()
    if not token and args.management_env_file:
        token = _env_file_value(
            Path(args.management_env_file), args.management_token_env
        )
    if not token:
        raise MatrixError(
            f"management token {args.management_token_env!r} is unavailable "
            "from process environment or configured env file"
        )
    return token


def parse_values(raw: str, *, setting: str) -> list[int]:
    if setting not in SUPPORTED_SETTINGS:
        raise MatrixError(f"unsupported endpoint setting: {setting}")
    minimum, maximum = SUPPORTED_SETTINGS[setting]
    values: list[int] = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError as exc:
            raise MatrixError(f"invalid endpoint value: {part!r}") from exc
        if not minimum <= value <= maximum:
            raise MatrixError(
                f"{setting} value {value} outside supported range "
                f"{minimum}..{maximum}"
            )
        if value not in values:
            values.append(value)
    if not values:
        raise MatrixError("endpoint matrix is empty")
    return values


def _request_json(
    method: str,
    url: str,
    *,
    token: str = "",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = None
    request_headers = {
        "Accept": "application/json",
        **(headers or {}),
    }
    if token:
        request_headers["X-Veetee-Management-Token"] = token
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise MatrixError(f"{method} {url} failed HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise MatrixError(f"{method} {url} failed: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MatrixError(f"{method} {url} returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise MatrixError(f"{method} {url} returned a non-object response")
    return parsed


async def request_json(*args, **kwargs) -> dict[str, Any]:
    return await asyncio.to_thread(_request_json, *args, **kwargs)


async def wait_for_http_ready(
    http_base: str,
    *,
    timeout_seconds: float = 60.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, float(timeout_seconds))
    last_error = ""
    while True:
        try:
            health = await request_json(
                "GET",
                f"{http_base.rstrip('/')}/health",
                timeout=2.0,
            )
            if str(health.get("liveness") or health.get("status") or "").lower() in {
                "alive",
                "healthy",
                "ready",
            }:
                return
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if loop.time() >= deadline:
            detail = f": {last_error}" if last_error else ""
            raise MatrixError(
                f"timed out waiting for VeeTee HTTP server readiness{detail}"
            )
        await asyncio.sleep(0.5)


async def ensure_benchmark_device_token(
    http_base: str,
    management_token: str,
) -> str:
    existing = os.getenv("VEETEE_DEVICE_TOKEN", "").strip()
    if existing:
        return existing

    device_id = os.getenv("VEETEE_DEVICE_ID", "").strip()
    client_id = os.getenv("VEETEE_CLIENT_ID", "").strip()
    temporary_device = not device_id and not client_id
    if temporary_device:
        nonce = uuid.uuid4().hex[:12]
        device_id = f"veetee-benchmark-{nonce}"
        client_id = f"benchmark-client-{nonce}"
    else:
        device_id = device_id or client_id
        client_id = client_id or device_id
    device_headers = {
        "Device-Id": device_id,
        "Client-Id": client_id,
        "Activation-Version": "1",
    }
    base = http_base.rstrip("/")
    ota = await request_json(
        "POST",
        f"{base}/ota/",
        headers=device_headers,
        payload={
            "application": {"version": "benchmark"},
            "board": {"type": "benchmark-client"},
        },
    )
    websocket = ota.get("websocket") or {}
    token = str(websocket.get("token") or "").strip()
    if token:
        os.environ["VEETEE_DEVICE_TOKEN"] = token
        os.environ["VEETEE_DEVICE_ID"] = device_id
        os.environ["VEETEE_CLIENT_ID"] = client_id
        os.environ["_VEETEE_BENCHMARK_TEMP_DEVICE"] = "1" if temporary_device else "0"
        return token

    activation = ota.get("activation") or {}
    code = str(activation.get("code") or "").strip()
    if not code:
        raise MatrixError(
            "OTA pairing did not return activation code or websocket token"
        )

    # Touch activation once to preserve the stock OTA lifecycle. A pending
    # response is expected before the management plane approves the code.
    try:
        await request_json(
            "POST",
            f"{base}/ota/activate",
            headers=device_headers,
            payload={},
        )
    except MatrixError as exc:
        if "HTTP 202" not in str(exc):
            raise

    await request_json(
        "POST",
        f"{base}/api/devices/pair",
        token=management_token,
        payload={
            "code": code,
            "assistant_id": "default",
            "name": "Benchmark Client",
        },
    )
    await request_json(
        "POST",
        f"{base}/ota/activate",
        headers=device_headers,
        payload={},
    )
    final = await request_json(
        "POST",
        f"{base}/ota/",
        headers=device_headers,
        payload={},
    )
    token = str((final.get("websocket") or {}).get("token") or "").strip()
    if not token:
        raise MatrixError("paired benchmark device did not receive websocket token")
    os.environ["VEETEE_DEVICE_TOKEN"] = token
    os.environ["VEETEE_DEVICE_ID"] = device_id
    os.environ["VEETEE_CLIENT_ID"] = client_id
    os.environ["_VEETEE_BENCHMARK_TEMP_DEVICE"] = "1" if temporary_device else "0"
    return token


async def cleanup_benchmark_device(
    http_base: str,
    management_token: str,
) -> str:
    if os.getenv("_VEETEE_BENCHMARK_TEMP_DEVICE", "") != "1":
        return ""
    device_id = os.getenv("VEETEE_DEVICE_ID", "").strip()
    client_id = os.getenv("VEETEE_CLIENT_ID", "").strip()
    if not device_id or not client_id:
        return "temporary benchmark device identity missing"
    error = ""
    try:
        await request_json(
            "POST",
            (
                f"{http_base.rstrip('/')}/api/devices/"
                f"{urllib.parse.quote(device_id, safe='')}/"
                f"{urllib.parse.quote(client_id, safe='')}/revoke"
            ),
            token=management_token,
            payload={},
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        os.environ.pop("VEETEE_DEVICE_TOKEN", None)
        os.environ.pop("VEETEE_DEVICE_ID", None)
        os.environ.pop("VEETEE_CLIENT_ID", None)
        os.environ.pop("_VEETEE_BENCHMARK_TEMP_DEVICE", None)
    return error


def diagnostic_runtime_value(
    diagnostics: dict[str, Any],
    key: str,
) -> Any:
    profile = diagnostics.get("profile", {})
    if key.startswith("asr."):
        return profile.get("asr", {}).get(key.split(".", 1)[1])
    if key.startswith("tts."):
        return profile.get("tts", {}).get(key.split(".", 1)[1])
    if key.startswith("latency."):
        return profile.get("latency", {}).get(key.split(".", 1)[1])
    prefix = "llm.speech_segmentation."
    if key.startswith(prefix):
        return profile.get("speech_segmentation", {}).get(key[len(prefix):])
    raise MatrixError(f"unsupported diagnostics runtime key: {key}")


def runtime_values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if isinstance(actual, bool):
            return False
        try:
            return abs(float(actual) - float(expected)) <= 1e-6
        except (TypeError, ValueError):
            return False
    return actual == expected


def runtime_restore_mismatches(
    diagnostics: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    mismatches: dict[str, dict[str, Any]] = {}
    for key, expected_value in expected.items():
        actual_value = diagnostic_runtime_value(diagnostics, key)
        if not runtime_values_match(expected_value, actual_value):
            mismatches[key] = {
                "expected": expected_value,
                "actual": actual_value,
            }
    return mismatches


def effective_endpoint_value(diagnostics: dict[str, Any], setting: str) -> int:
    value = diagnostic_runtime_value(diagnostics, setting)
    if isinstance(value, bool):
        raise MatrixError(f"diagnostics missing effective {setting}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MatrixError(f"diagnostics missing effective {setting}") from exc


def server_latency_summary(
    diagnostics: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, dict[str, float | int | None]]:
    measured = [record for record in records if record.get("measured")]
    session_ids = {
        str(record.get("session_id") or "")
        for record in measured
        if str(record.get("session_id") or "")
    }
    turns = [
        item
        for item in (diagnostics.get("runtime", {}).get("latest_turns") or [])
        if str(item.get("session_id") or "") in session_ids
        and item.get("source") == "asr"
    ]
    if measured:
        turns = turns[-len(measured):]
    values: dict[str, list[float]] = {}
    for turn in turns:
        for name, raw in (turn.get("latency_ms") or {}).items():
            if isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            values.setdefault(str(name), []).append(value)
    return {
        name: {
            "samples": len(items),
            "p50": benchmark_pipeline._percentile(items, 0.50),
            "p95": benchmark_pipeline._percentile(items, 0.95),
            "max": round(max(items), 3) if items else None,
        }
        for name, items in values.items()
        if items
    }


def matrix_row(
    value: int,
    summary: dict[str, Any],
    *,
    server_latency_ms: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "value_ms": int(value),
        "requested_samples": summary.get("requested_samples"),
        "successful_samples": summary.get("successful_samples"),
        "success_rate": summary.get("success_rate"),
        "p50_ms": summary.get("p50_ms"),
        "p90_ms": summary.get("p90_ms"),
        "p95_ms": summary.get("p95_ms"),
        "max_ms": summary.get("max_ms"),
        "sla_status": summary.get("sla_status"),
        "stage_latency_ms": summary.get("stage_latency_ms", {}),
        "server_latency_ms": server_latency_ms or {},
        "quality": summary.get("quality", {}),
    }


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


async def apply_values(
    http_base: str,
    token: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    return await request_json(
        "PATCH",
        f"{http_base.rstrip('/')}/api/runtime-config",
        token=token,
        payload={"values": values},
        timeout=30.0,
    )


async def apply_setting(http_base: str, token: str, setting: str, value: int) -> dict[str, Any]:
    return await apply_values(
        http_base,
        token,
        {setting: int(value)},
    )


def active_session_count(diagnostics: dict[str, Any]) -> int:
    active = diagnostics.get("server", {}).get("active_sessions", 0)
    try:
        return max(0, int(active or 0))
    except (TypeError, ValueError):
        return 0


async def wait_for_quiescent_sessions(
    http_base: str,
    token: str,
    *,
    timeout_seconds: float,
    max_active_sessions: int = 0,
) -> None:
    allowed = max(0, int(max_active_sessions))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.1, float(timeout_seconds))
    while True:
        diagnostics = await request_json(
            "GET",
            f"{http_base.rstrip('/')}/api/diagnostics",
            token=token,
        )
        active_count = active_session_count(diagnostics)
        if active_count <= allowed:
            return
        if loop.time() >= deadline:
            raise MatrixError(
                "timed out waiting for benchmark sessions to drain "
                f"(active={active_count}, baseline={allowed})"
            )
        await asyncio.sleep(0.1)


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
    original = effective_endpoint_value(diagnostics, args.setting)
    asr_profile = diagnostics.get("profile", {}).get("asr", {})
    original_speculative = {
        "asr.speculative_inference_enabled": bool(
            asr_profile.get("speculative_inference_enabled", False)
        ),
        "asr.speculative_start_silence_ms": int(
            asr_profile.get("speculative_start_silence_ms", 64)
        ),
        "asr.speculative_min_confidence": float(
            asr_profile.get("speculative_min_confidence", 0.95)
        ),
        "asr.speculative_llm_enabled": bool(
            asr_profile.get("speculative_llm_enabled", False)
        ),
        "asr.speculative_llm_min_confidence": float(
            asr_profile.get("speculative_llm_min_confidence", 0.95)
        ),
    }
    original_vad_end_threshold = float(
        asr_profile.get("vad_end_threshold", asr_profile.get("vad_threshold_low", 0.3))
    )
    speculative_override = args.speculative != "keep"
    speculative_llm_override = args.speculative_llm != "keep"
    vad_end_override = args.vad_end_threshold is not None

    effective_speculative_asr = (
        args.speculative == "on"
        if speculative_override
        else original_speculative["asr.speculative_inference_enabled"]
    )
    if (
        args.speculative == "on"
        and args.speculative_start_silence_ms >= min(values)
    ):
        raise MatrixError(
            "speculative start silence must be lower than every endpoint "
            "value in the matrix"
        )
    if args.speculative_llm == "on" and not effective_speculative_asr:
        raise MatrixError(
            "speculative LLM requires speculative ASR; use --speculative on "
            "or enable speculative ASR in runtime first"
        )

    if speculative_override or speculative_llm_override:
        await wait_for_quiescent_sessions(
            http_base,
            token,
            timeout_seconds=args.quiesce_timeout,
            max_active_sessions=baseline_active_sessions,
        )
        speculative_values: dict[str, Any] = {}
        if speculative_override:
            speculative_values.update({
                "asr.speculative_inference_enabled": args.speculative == "on",
                "asr.speculative_start_silence_ms": args.speculative_start_silence_ms,
                "asr.speculative_min_confidence": args.speculative_min_confidence,
            })
        if speculative_llm_override:
            speculative_values.update({
                "asr.speculative_llm_enabled": args.speculative_llm == "on",
                "asr.speculative_llm_min_confidence": args.speculative_llm_min_confidence,
            })
        applied_speculative = await apply_values(
            http_base,
            token,
            speculative_values,
        )
        if not applied_speculative.get("applied", False):
            raise MatrixError("runtime did not confirm speculative override")

    if vad_end_override:
        await wait_for_quiescent_sessions(
            http_base,
            token,
            timeout_seconds=args.quiesce_timeout,
            max_active_sessions=baseline_active_sessions,
        )
        applied_vad = await apply_values(
            http_base,
            token,
            {"asr.vad_end_threshold": float(args.vad_end_threshold)},
        )
        if not applied_vad.get("applied", False):
            raise MatrixError("runtime did not confirm VAD end-threshold override")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.artifact_dir) / f"endpoint-matrix-{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    restore_error = ""
    cleanup_error = ""
    expected_restored_values: dict[str, Any] = {
        args.setting: original,
        **original_speculative,
        "asr.vad_end_threshold": original_vad_end_threshold,
    }
    runtime_modified = speculative_override or speculative_llm_override or vad_end_override
    current_value = original
    try:
        for index, value in enumerate(values):
            needs_apply = value != current_value
            if needs_apply:
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
                current_value = value
                runtime_modified = True

            variant_dir = root / f"{args.setting.replace('.', '-')}-{value}ms"
            variant_dir.mkdir(parents=True, exist_ok=True)
            records, summary = await benchmark_pipeline.run(
                benchmark_args(args, variant_dir)
            )
            diagnostics_after = await request_json(
                "GET",
                f"{http_base}/api/diagnostics",
                token=token,
            )
            server_latency_ms = server_latency_summary(diagnostics_after, records)
            labelled_samples = int(
                (summary.get("quality") or {}).get("labelled_samples") or 0
            )
            summary.update({
                "endpoint_setting": args.setting,
                "endpoint_value_ms": value,
                "quality_not_measured": labelled_samples <= 0,
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
            rows.append(
                matrix_row(
                    value,
                    summary,
                    server_latency_ms=server_latency_ms,
                )
            )
    finally:
        if runtime_modified:
            try:
                await wait_for_http_ready(
                    http_base,
                    timeout_seconds=args.startup_timeout,
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
                restore_values: dict[str, Any] = {args.setting: original}
                if speculative_override or speculative_llm_override:
                    restore_values.update(original_speculative)
                if vad_end_override:
                    restore_values["asr.vad_end_threshold"] = original_vad_end_threshold
                await apply_values(http_base, token, restore_values)
            except Exception as exc:  # restoration must be visible in artifact/output
                restore_error = f"{type(exc).__name__}: {exc}"
        try:
            await wait_for_http_ready(
                http_base,
                timeout_seconds=args.startup_timeout,
            )
            restored_diagnostics = await request_json(
                "GET",
                f"{http_base}/api/diagnostics",
                token=token,
            )
            mismatches = runtime_restore_mismatches(
                restored_diagnostics,
                expected_restored_values,
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
                timeout_seconds=args.startup_timeout,
            )
            cleanup_error = await cleanup_benchmark_device(http_base, token)
        except Exception as exc:
            cleanup_error = f"{type(exc).__name__}: {exc}"

    aggregate = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "setting": args.setting,
        "values_ms": values,
        "original_value_ms": original,
        "restored": not restore_error,
        "restore_error": restore_error or None,
        "benchmark_device_revoked": not cleanup_error,
        "benchmark_device_cleanup_error": cleanup_error or None,
        "fixture": args.wav,
        "mode": args.mode,
        "runs_per_value": args.runs,
        "warmup_per_value": args.warmup,
        "certification_mode": bool(args.certification),
        "quality_not_measured": not any(
            int((row.get("quality") or {}).get("labelled_samples") or 0) > 0
            for row in rows
        ),
        "speculative_override": (
            None
            if args.speculative == "keep"
            else {
                "enabled": args.speculative == "on",
                "start_silence_ms": args.speculative_start_silence_ms,
                "min_confidence": args.speculative_min_confidence,
            }
        ),
        "speculative_llm_override": (
            None
            if args.speculative_llm == "keep"
            else {
                "enabled": args.speculative_llm == "on",
                "min_confidence": args.speculative_llm_min_confidence,
            }
        ),
        "vad_end_threshold_override": args.vad_end_threshold,
        "selection_policy": (
            "Do not choose a production endpoint from latency alone. "
            "Require labelled false-endpoint and WER/CER acceptance."
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
        description="Hot-apply endpoint values and benchmark the same labelled voice fixture"
    )
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--wav", required=True)
    parser.add_argument("--speech-end-sample", type=int, default=None)
    parser.add_argument(
        "--setting",
        choices=tuple(SUPPORTED_SETTINGS),
        default="asr.min_silence_duration_ms",
    )
    parser.add_argument("--values", default="450,320,256,192")
    parser.add_argument(
        "--speculative",
        choices=("keep", "on", "off"),
        default="keep",
        help="optionally hot-override speculative local ASR for this matrix",
    )
    parser.add_argument(
        "--speculative-start-silence-ms",
        type=int,
        default=64,
    )
    parser.add_argument(
        "--speculative-min-confidence",
        type=float,
        default=0.95,
    )
    parser.add_argument(
        "--speculative-llm",
        choices=("keep", "on", "off"),
        default="keep",
        help="optionally start a side-effect-free buffered AI turn during trailing silence",
    )
    parser.add_argument(
        "--speculative-llm-min-confidence",
        type=float,
        default=0.95,
    )
    parser.add_argument(
        "--vad-end-threshold",
        type=float,
        default=None,
        help="optional constant Silero end threshold for the endpoint matrix",
    )
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
        help="quiet period between endpoint variants",
    )
    parser.add_argument(
        "--quiesce-timeout",
        type=float,
        default=15.0,
        help="wait for active_sessions=0 before hot-applying the next variant",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=60.0,
        help="wait for the HTTP management plane after a cold service restart",
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
        or args.quiesce_timeout <= 0
        or args.startup_timeout <= 0
        or not 32 <= args.speculative_start_silence_ms <= 1000
        or not 0 <= args.speculative_min_confidence <= 1
        or not 0 <= args.speculative_llm_min_confidence <= 1
        or (
            args.vad_end_threshold is not None
            and not 0.01 <= args.vad_end_threshold <= 0.99
        )
    ):
        parser.error(
            "runs/warmup/timeout/cooldown/quiesce/speculative/VAD values are invalid"
        )
    return args


def main() -> int:
    args = parse_args()
    try:
        with benchmark_run_lock("endpoint-matrix"):
            result = asyncio.run(run_matrix(args))
    except (MatrixError, benchmark_pipeline.BenchmarkError) as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("restored") or not result.get("benchmark_device_revoked"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
