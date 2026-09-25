#!/usr/bin/env python3
"""Concurrent authenticated text-turn load probe against the live VeeTee server.

Each worker represents one paired device, holds one WebSocket session and runs
K sequential text turns. The probe exercises LLM/TTS/context contention but
not ASR audio decode. Benchmark identities are paired through the normal OTA
flow and revoked after each level; auth is never bypassed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import uuid
from dataclasses import dataclass

try:
    from scripts.benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )
except ModuleNotFoundError:
    from benchmark_endpoint_matrix import (
        MatrixError,
        benchmark_run_lock,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )


QUESTIONS = [
    "Mấy giờ rồi?",
    "Hôm nay là thứ mấy?",
    "Tính giúp tôi 17 nhân 23.",
    "Kể một câu thật ngắn về con mèo.",
    "Thời tiết hôm nay thế nào?",
]


@dataclass(frozen=True)
class ProbeDevice:
    device_id: str
    client_id: str
    token: str

    @property
    def websocket_headers(self) -> dict[str, str]:
        return {
            "Device-Id": self.device_id,
            "Client-Id": self.client_id,
            "Protocol-Version": "1",
            "Authorization": f"Bearer {self.token}",
        }


async def pair_probe_device(
    http_base: str,
    management_token: str,
    *,
    ordinal: int,
    owner_id: str = "",
    name: str = "Load Probe",
) -> ProbeDevice:
    nonce = uuid.uuid4().hex[:10]
    device_id = f"veetee-load-{ordinal}-{nonce}"
    client_id = f"load-client-{ordinal}-{nonce}"
    headers = {
        "Device-Id": device_id,
        "Client-Id": client_id,
        "Activation-Version": "1",
    }
    base = http_base.rstrip("/")
    first = await request_json(
        "POST",
        f"{base}/ota/",
        headers=headers,
        payload={
            "application": {"version": "load-probe"},
            "board": {"type": "benchmark-client"},
        },
    )
    token = str((first.get("websocket") or {}).get("token") or "").strip()
    if not token:
        code = str((first.get("activation") or {}).get("code") or "").strip()
        if not code:
            raise MatrixError("load probe OTA did not return activation code")
        try:
            await request_json(
                "POST",
                f"{base}/ota/activate",
                headers=headers,
                payload={},
            )
        except MatrixError as exc:
            if "HTTP 202" not in str(exc):
                raise
        pair_payload = {
            "code": code,
            "assistant_id": "default",
            "name": str(name or "Load Probe"),
        }
        if owner_id:
            pair_payload["owner_id"] = str(owner_id)
        await request_json(
            "POST",
            f"{base}/api/devices/pair",
            token=management_token,
            payload=pair_payload,
        )
        await request_json(
            "POST",
            f"{base}/ota/activate",
            headers=headers,
            payload={},
        )
        final = await request_json(
            "POST",
            f"{base}/ota/",
            headers=headers,
            payload={},
        )
        token = str((final.get("websocket") or {}).get("token") or "").strip()
    if not token:
        raise MatrixError("paired load probe device did not receive websocket token")
    return ProbeDevice(device_id=device_id, client_id=client_id, token=token)


async def revoke_probe_device(
    http_base: str,
    management_token: str,
    device: ProbeDevice,
) -> str:
    did = urllib.parse.quote(device.device_id, safe="")
    cid = urllib.parse.quote(device.client_id, safe="")
    try:
        await request_json(
            "POST",
            f"{http_base.rstrip('/')}/api/devices/{did}/{cid}/revoke",
            token=management_token,
            payload={},
        )
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def one_turn(ws, text: str, timeout: float) -> tuple[bool, float, int]:
    start = time.monotonic()
    await ws.send(json.dumps({"type": "text", "text": text}))
    binaries = 0
    try:
        deadline = start + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
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


async def worker(
    uri: str,
    turns: int,
    timeout: float,
    device: ProbeDevice,
    *,
    turn_cooldown_ms: int = 150,
    question: str = "",
) -> dict:
    from websockets.asyncio.client import connect

    latencies: list[float] = []
    turn_records: list[dict] = []
    completed = failed = binaries = 0
    try:
        async with connect(
            uri,
            max_size=8 * 1024 * 1024,
            additional_headers=device.websocket_headers,
        ) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            }))
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=min(timeout, 10.0))
            if not isinstance(hello_raw, str):
                raise RuntimeError("server hello was not JSON text")
            hello = json.loads(hello_raw)
            session_id = str(hello.get("session_id") or "").strip()
            if hello.get("type") != "hello" or not session_id:
                raise RuntimeError(f"invalid server hello: {hello!r}")
            for index in range(turns):
                text = question or QUESTIONS[index % len(QUESTIONS)]
                ok, latency, nb = await one_turn(
                    ws,
                    text,
                    timeout,
                )
                binaries += nb
                turn_id = f"{session_id}:{index + 1}"
                turn_records.append({
                    "turn_id": turn_id,
                    "protocol_completed": bool(ok),
                    "latency_s": latency,
                    "binaries": nb,
                })
                if ok:
                    completed += 1
                    latencies.append(latency)
                else:
                    failed += 1
                if turn_cooldown_ms > 0 and index + 1 < turns:
                    await asyncio.sleep(turn_cooldown_ms / 1000.0)
    except Exception as exc:
        failed += turns
        return {
            "completed": 0,
            "failed": failed,
            "latencies": [],
            "binaries": 0,
            "session_id": "",
            "turn_records": [],
            "error": str(exc)[:160],
        }
    return {
        "completed": completed,
        "failed": failed,
        "latencies": latencies,
        "binaries": binaries,
        "session_id": session_id,
        "turn_records": turn_records,
    }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(pct / 100 * len(ordered)))
    return ordered[index]


def reconcile_level_results(
    results: list[dict],
    server_metrics: dict,
    turns_requested: int,
) -> dict:
    """Reconcile protocol completion with authoritative server turn outcomes.

    Receiving recovery audio and tts:stop proves the transport stayed usable,
    not that the AI turn succeeded. A load sample is successful only when the
    server emitted a completed turn trace for the exact turn id.
    """
    turn_records = [
        record
        for result in results
        for record in (result.get("turn_records") or [])
        if record.get("turn_id")
    ]
    completed_ids = set(server_metrics.pop("_completed_turn_ids", []) or [])
    observed_ids = set(server_metrics.pop("_observed_turn_ids", []) or [])

    authoritative_records = [
        record for record in turn_records
        if str(record.get("turn_id") or "") in completed_ids
    ]
    successful_latencies = [
        float(record["latency_s"])
        for record in authoritative_records
        if record.get("protocol_completed") and record.get("latency_s") is not None
    ]
    completed = len(authoritative_records)
    failed = max(0, int(turns_requested) - completed)
    protocol_completed = sum(
        1 for record in turn_records if record.get("protocol_completed")
    )
    protocol_failed = max(0, int(turns_requested) - protocol_completed)
    observed_count = len({
        str(record.get("turn_id") or "")
        for record in turn_records
        if str(record.get("turn_id") or "") in observed_ids
    })
    unobserved_turns = max(0, int(turns_requested) - observed_count)

    return {
        "completed": completed,
        "failed": failed,
        "success_rate": (
            completed / turns_requested if turns_requested else 0.0
        ),
        "p50_s": (
            statistics.median(successful_latencies)
            if successful_latencies else None
        ),
        "p95_s": percentile(successful_latencies, 95),
        "max_s": max(successful_latencies) if successful_latencies else None,
        "protocol_completed": protocol_completed,
        "protocol_failed": protocol_failed,
        "protocol_success_rate": (
            protocol_completed / turns_requested if turns_requested else 0.0
        ),
        "observed_turns": observed_count,
        "unobserved_turns": unobserved_turns,
        "trace_complete": observed_count == int(turns_requested),
    }


def summarize_server_metrics(
    diagnostics: dict,
    session_ids: set[str],
) -> dict:
    observed = [
        turn
        for turn in (diagnostics.get("runtime", {}).get("latest_turns") or [])
        if str(turn.get("session_id") or "") in session_ids
    ]
    outcomes: dict[str, int] = {}
    for turn in observed:
        outcome = str(turn.get("outcome") or "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    turns = [
        turn for turn in observed
        if turn.get("outcome") == "completed"
    ]
    fields = (
        "turn_start_to_first_ws_binary_ms",
        "llm_headers",
        "llm_first_speech_segment",
        "tts_queue_to_lock",
        "tts_first_pcm",
        "tts_lease_held",
    )
    result = {
        "observed_turns": len(observed),
        "completed_turns": len(turns),
        "outcomes": outcomes,
        "_observed_turn_ids": [
            str(turn.get("turn_id") or "") for turn in observed
            if turn.get("turn_id")
        ],
        "_completed_turn_ids": [
            str(turn.get("turn_id") or "") for turn in turns
            if turn.get("turn_id")
        ],
    }
    for field in fields:
        if field == "turn_start_to_first_ws_binary_ms":
            values = [
                float(turn[field])
                for turn in turns
                if turn.get(field) is not None
            ]
        else:
            values = [
                float(turn.get("latency_ms", {}).get(field))
                for turn in turns
                if turn.get("latency_ms", {}).get(field) is not None
            ]
        result[field] = {
            "samples": len(values),
            "p50_ms": (
                round(statistics.median(values), 3)
                if values else None
            ),
            "p95_ms": (
                round(percentile(values, 95), 3)
                if values else None
            ),
            "max_ms": round(max(values), 3) if values else None,
        }

    aggregate_fields = {
        "tts_queue_wait_max": ("tts_queue_wait", "max_ms"),
        "tts_queue_wait_total": ("tts_queue_wait", "total_ms"),
        "tts_lease_held_max": ("tts_lease_held", "max_ms"),
        "tts_lease_held_total": ("tts_lease_held", "total_ms"),
        "tts_inference_max": ("tts_inference", "max_ms"),
        "tts_inference_total": ("tts_inference", "total_ms"),
    }
    for output_name, (aggregate_name, field_name) in aggregate_fields.items():
        values = []
        for turn in turns:
            raw = (
                turn.get("stage_aggregates_ms", {})
                .get(aggregate_name, {})
                .get(field_name)
            )
            if raw is None or isinstance(raw, bool):
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        result[output_name] = {
            "samples": len(values),
            "p50_ms": round(statistics.median(values), 3) if values else None,
            "p95_ms": round(percentile(values, 95), 3) if values else None,
            "max_ms": round(max(values), 3) if values else None,
        }

    segment_counts = [
        int(turn.get("stage_aggregates_ms", {}).get("tts_lease_held", {}).get("count"))
        for turn in turns
        if turn.get("stage_aggregates_ms", {}).get("tts_lease_held", {}).get("count")
        is not None
    ]
    result["tts_segment_count"] = {
        "samples": len(segment_counts),
        "p50": (
            round(statistics.median(segment_counts), 3)
            if segment_counts else None
        ),
        "max": max(segment_counts) if segment_counts else None,
    }
    return result


async def run_level(
    uri: str,
    sessions: int,
    turns: int,
    timeout: float,
    *,
    http_base: str,
    management_token: str,
    turn_cooldown_ms: int,
    metrics_settle_ms: int,
    question: str = "",
) -> dict:
    devices: list[ProbeDevice] = []
    cleanup_errors: list[str] = []
    try:
        devices = list(await asyncio.gather(*(
            pair_probe_device(
                http_base,
                management_token,
                ordinal=index + 1,
            )
            for index in range(sessions)
        )))
        results = await asyncio.gather(*(
            worker(
                uri,
                turns,
                timeout,
                device,
                turn_cooldown_ms=turn_cooldown_ms,
                question=question,
            )
            for device in devices
        ))
        session_ids = {
            str(result.get("session_id") or "")
            for result in results
            if result.get("session_id")
        }
        if metrics_settle_ms > 0:
            await asyncio.sleep(metrics_settle_ms / 1000.0)
        diagnostics = await request_json(
            "GET",
            f"{http_base.rstrip('/')}/api/diagnostics",
            token=management_token,
        )
        server_metrics = summarize_server_metrics(diagnostics, session_ids)
        turns_requested = sessions * turns
        reconciled = reconcile_level_results(
            results,
            server_metrics,
            turns_requested,
        )
        return {
            "sessions": sessions,
            "turns_requested": turns_requested,
            **reconciled,
            "binaries": sum(result["binaries"] for result in results),
            "errors": [
                result["error"] for result in results if "error" in result
            ],
            "auth_mode": "paired_ephemeral_devices",
            "server_metrics": server_metrics,
        }
    finally:
        if devices:
            cleanup_errors = [
                error
                for error in await asyncio.gather(*(
                    revoke_probe_device(http_base, management_token, device)
                    for device in devices
                ))
                if error
            ]
        if cleanup_errors:
            print(
                "WARN: load probe cleanup: " + "; ".join(cleanup_errors),
                file=sys.stderr,
                flush=True,
            )


async def async_main(args: argparse.Namespace) -> int:
    http_base = args.health.rsplit("/health", 1)[0].rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=30.0)
    management_token = load_management_token(args)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "levels": [],
        "note": (
            "text turns only; paired/authenticated sessions; no ASR audio decode load. "
            "Top-level success is authoritative server turn_finish outcome=completed; "
            "protocol TTS completion (including recovery audio) is reported separately."
        ),
    }
    for level in (int(value) for value in args.levels.split(",") if value.strip()):
        print(
            f"--- level: {level} sessions x {args.turns} turns ---",
            flush=True,
        )
        result = await run_level(
            args.uri,
            level,
            args.turns,
            args.timeout,
            http_base=http_base,
            management_token=management_token,
            turn_cooldown_ms=args.turn_cooldown_ms,
            metrics_settle_ms=args.metrics_settle_ms,
            question=args.question,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        report["levels"].append(result)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_path = artifact_dir / f"load-probe-{stamp}.json"
    report["artifact_path"] = str(artifact_path)
    artifact_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("=== LOAD REPORT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--health", default="http://127.0.0.1:8003/health")
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--turn-cooldown-ms", type=int, default=150)
    parser.add_argument("--metrics-settle-ms", type=int, default=300)
    parser.add_argument(
        "--question",
        default="",
        help="optional fixed prompt for every turn; default cycles built-in probe questions",
    )
    parser.add_argument("--artifact-dir", default="benchmark-artifacts")
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    args = parser.parse_args()
    if (
        args.turns < 1
        or args.timeout <= 0
        or args.turn_cooldown_ms < 0
        or args.metrics_settle_ms < 0
    ):
        parser.error("turns/timeout/cooldown/settle values are invalid")

    try:
        with benchmark_run_lock("load-probe"):
            return asyncio.run(async_main(args))
    except MatrixError as exc:
        print(f"BLOCKED: {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
