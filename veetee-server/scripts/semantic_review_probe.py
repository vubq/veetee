#!/usr/bin/env python3
"""Collect live semantic-review evidence without auto-grading AI semantics.

The runner sends selected human-review cases through the authenticated stock
WebSocket text path, captures protocol output and the retained server turn
trace, then writes review packets with an empty human verdict.

Held-out cases are excluded by default so prompt/model tuning cannot
accidentally consume evaluation data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.benchmark_endpoint_matrix import (
        MatrixError,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )
    from scripts.load_probe import pair_probe_device, revoke_probe_device
except ModuleNotFoundError:
    from benchmark_endpoint_matrix import (
        MatrixError,
        load_management_token,
        request_json,
        wait_for_http_ready,
    )
    from load_probe import pair_probe_device, revoke_probe_device


def load_cases(
    path: str,
    *,
    include_held_out: bool,
    ids: set[str],
    group: str,
    limit: int,
) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise MatrixError("semantic review queue must be a JSON list")

    selected: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "").strip()
        if not case_id:
            continue
        if ids and case_id not in ids:
            continue
        if group and str(item.get("group") or "") != group:
            continue
        if item.get("split") == "held-out" and not include_held_out:
            continue
        selected.append(dict(item))
        if limit > 0 and len(selected) >= limit:
            break

    if ids:
        found = {str(item.get("id") or "") for item in selected}
        missing = sorted(ids - found)
        if missing:
            suffix = (
                " (held-out cases require --include-held-out)"
                if not include_held_out
                else ""
            )
            raise MatrixError(
                "requested semantic case(s) unavailable: "
                + ", ".join(missing)
                + suffix
            )
    if not selected:
        raise MatrixError("no semantic review cases selected")
    return selected


async def collect_protocol_turn(ws, text: str, *, timeout: float) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + timeout
    messages: list[dict[str, Any]] = []
    binary_frames = 0
    completed = False
    closed = False

    await ws.send(json.dumps({"type": "text", "text": text}, ensure_ascii=False))
    while loop.time() < deadline:
        remaining = max(0.01, deadline - loop.time())
        try:
            payload = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except Exception:
            closed = True
            break

        if isinstance(payload, bytes):
            binary_frames += 1
            continue
        try:
            item = json.loads(payload)
        except Exception:
            messages.append({"type": "unparsed_text", "text": str(payload)[:500]})
            continue
        if isinstance(item, dict):
            # Keep management-review evidence but cap large/unexpected payloads.
            encoded = json.dumps(item, ensure_ascii=False, default=str)
            if len(encoded) > 4000:
                item = {"type": item.get("type"), "truncated": True}
            messages.append(item)
            if item.get("type") == "tts" and item.get("state") == "stop":
                completed = True
                break

    return {
        "protocol_completed": completed,
        "connection_closed": closed,
        "elapsed_ms": round((loop.time() - started) * 1000.0, 3),
        "binary_frames": binary_frames,
        "messages": messages,
    }


async def fetch_turn_detail(
    http_base: str,
    management_token: str,
    turn_id: str,
    *,
    settle_ms: int,
) -> dict[str, Any]:
    if settle_ms > 0:
        await asyncio.sleep(settle_ms / 1000.0)
    quoted = urllib.parse.quote(turn_id, safe="")
    try:
        return await request_json(
            "GET",
            f"{http_base.rstrip('/')}/api/turns/{quoted}",
            token=management_token,
        )
    except MatrixError as exc:
        return {
            "error": str(exc),
            "turn_id": turn_id,
        }


async def run_case(
    uri: str,
    http_base: str,
    management_token: str,
    device,
    case: dict[str, Any],
    *,
    timeout: float,
    settle_ms: int,
) -> dict[str, Any]:
    from websockets.asyncio.client import connect

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
        hello_raw = await asyncio.wait_for(ws.recv(), timeout=min(10.0, timeout))
        if not isinstance(hello_raw, str):
            raise MatrixError("semantic review server hello was not JSON")
        hello = json.loads(hello_raw)
        session_id = str(hello.get("session_id") or "").strip()
        if hello.get("type") != "hello" or not session_id:
            raise MatrixError(f"invalid semantic review hello: {hello!r}")

        observed = await collect_protocol_turn(
            ws,
            str(case.get("user") or ""),
            timeout=timeout,
        )
        turn_id = f"{session_id}:1"

    turn_detail = await fetch_turn_detail(
        http_base,
        management_token,
        turn_id,
        settle_ms=settle_ms,
    )
    return {
        "id": case.get("id"),
        "split": case.get("split"),
        "group": case.get("group"),
        "critical": bool(case.get("critical")),
        "user": case.get("user"),
        "expected_semantic_outcome": case.get("expected_semantic_outcome"),
        "evidence_needed": case.get("evidence_needed") or [],
        "review_status": case.get("review_status"),
        "observed": {
            **observed,
            "session_id": session_id,
            "turn_id": turn_id,
            "turn_detail": turn_detail,
        },
        "human_verdict": None,
        "reviewer_notes": "",
    }


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    http_base = args.health.rsplit("/health", 1)[0].rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=30.0)
    management_token = load_management_token(args)
    ids = {
        item.strip()
        for item in str(args.ids or "").split(",")
        if item.strip()
    }
    cases = load_cases(
        args.queue,
        include_held_out=args.include_held_out,
        ids=ids,
        group=args.group,
        limit=args.limit,
    )

    device = await pair_probe_device(
        http_base,
        management_token,
        ordinal=1,
    )
    packets: list[dict[str, Any]] = []
    cleanup_error = ""
    try:
        for index, case in enumerate(cases):
            packets.append(
                await run_case(
                    args.uri,
                    http_base,
                    management_token,
                    device,
                    case,
                    timeout=args.timeout,
                    settle_ms=args.metrics_settle_ms,
                )
            )
            if args.case_cooldown_ms > 0 and index + 1 < len(cases):
                await asyncio.sleep(args.case_cooldown_ms / 1000.0)
    finally:
        cleanup_error = await revoke_probe_device(
            http_base,
            management_token,
            device,
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": args.queue,
        "include_held_out": bool(args.include_held_out),
        "cases": len(packets),
        "cleanup_error": cleanup_error or None,
        "review_policy": (
            "No automatic semantic pass/fail. A human reviewer must compare "
            "expected_semantic_outcome/evidence_needed with observed protocol "
            "and server turn trace."
        ),
        "results": packets,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["output"] = str(output)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue",
        default="eval/semantic_corpus_review_queue.json",
    )
    parser.add_argument(
        "--output",
        default="eval/semantic_review_evidence.json",
    )
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--health", default="http://127.0.0.1:8003/health")
    parser.add_argument("--ids", default="")
    parser.add_argument("--group", default="")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--include-held-out", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--case-cooldown-ms", type=int, default=300)
    parser.add_argument("--metrics-settle-ms", type=int, default=150)
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    args = parser.parse_args()
    if (
        args.limit < 0
        or args.timeout <= 0
        or args.case_cooldown_ms < 0
        or args.metrics_settle_ms < 0
    ):
        parser.error("limit/timeout/cooldown values are invalid")
    return args


def main() -> int:
    args = parse_args()
    try:
        report = asyncio.run(async_main(args))
    except MatrixError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1
    print(json.dumps({
        "output": report["output"],
        "cases": report["cases"],
        "cleanup_error": report["cleanup_error"],
        "include_held_out": report["include_held_out"],
    }, ensure_ascii=False))
    return 0 if not report["cleanup_error"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
