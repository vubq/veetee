#!/usr/bin/env python3
"""Durable-memory deployment acceptance with isolated synthetic owners.

Use two phases around an explicit operator/service restart:

  1. --phase prepare
  2. restart the service outside this script
  3. --phase resume

The state file contains only random acceptance owner/marker IDs and fact IDs.
Resume verifies that the service MainPID changed, then exercises the real
authenticated device -> owner -> ContextBuilder path. All acceptance facts
are physically removed in a finally block. No existing user-owned fact is
read, mutated, or deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config.settings import load_settings
from core.memory.store import MemoryStore
from scripts.benchmark_endpoint_matrix import (
    MatrixError,
    load_management_token,
    request_json,
    wait_for_http_ready,
)
from scripts.load_probe import (
    pair_probe_device,
    revoke_probe_device,
    worker,
)


def _safe_database_path(raw: str) -> Path:
    path = Path(str(raw or "").strip())
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _service_main_pid(service: str) -> int:
    try:
        raw = subprocess.check_output(
            ["systemctl", "show", service, "--property=MainPID", "--value"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        ).strip()
        return int(raw or 0)
    except Exception as exc:
        raise MatrixError(
            f"cannot read MainPID for service {service!r}: {type(exc).__name__}"
        ) from exc


def _purge_acceptance_owners(database_path: Path, owner_ids: list[str]) -> None:
    if not database_path.exists() or not owner_ids:
        return
    if any(not owner.startswith("veetee-accept-") for owner in owner_ids):
        raise RuntimeError("refusing to purge non-acceptance owner")
    placeholders = ",".join("?" for _ in owner_ids)
    conn = sqlite3.connect(database_path, timeout=2.0)
    try:
        conn.execute(
            f"DELETE FROM memory_facts WHERE owner_id IN ({placeholders})",
            owner_ids,
        )
        conn.commit()
    finally:
        conn.close()


async def _active_sessions(http_base: str, token: str) -> int:
    diagnostics = await request_json(
        "GET",
        f"{http_base.rstrip('/')}/api/diagnostics",
        token=token,
    )
    return int((diagnostics.get("server") or {}).get("active_sessions") or 0)


def _load_memory_config(args: argparse.Namespace):
    config = load_settings(args.config)
    if not config.memory.enabled:
        raise MatrixError("memory.enabled is false")
    if not config.memory.durable_enabled:
        raise MatrixError("memory.durable_enabled is false")
    return config, _safe_database_path(config.memory.database_path)


def _event(detail: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in detail.get("events") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


async def run_context_probe(
    *,
    uri: str,
    http_base: str,
    management_token: str,
    device,
    query: str,
    timeout: float,
) -> dict[str, Any]:
    result = await worker(
        uri,
        1,
        timeout,
        device,
        turn_cooldown_ms=0,
        question=query,
    )
    records = result.get("turn_records") or []
    if not records:
        raise MatrixError(
            f"context probe failed before turn trace: {result.get('error') or result}"
        )
    turn_id = str(records[0].get("turn_id") or "")
    if not turn_id:
        raise MatrixError("context probe did not produce turn_id")
    await asyncio.sleep(0.15)
    detail = await request_json(
        "GET",
        f"{http_base.rstrip('/')}/api/turns/{turn_id}",
        token=management_token,
    )
    memory = _event(detail, "context_memory") or {}
    return {
        "turn_id": turn_id,
        "protocol_completed": bool(records[0].get("protocol_completed")),
        "outcome": detail.get("outcome"),
        "memory_status": memory.get("status"),
        "memory_returned": int(memory.get("returned") or 0),
        "durable_ids": [
            str(item)
            for item in (memory.get("durable_ids") or [])
            if str(item)
        ],
    }


async def prepare(args: argparse.Namespace) -> dict[str, Any]:
    config, database_path = _load_memory_config(args)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=30.0)
    token = load_management_token(args)
    active = await _active_sessions(http_base, token)
    if active:
        raise MatrixError(
            f"refusing acceptance prepare while active_sessions={active}"
        )

    suffix = uuid.uuid4().hex[:16]
    owner_a = f"veetee-accept-a-{suffix}"
    owner_b = f"veetee-accept-b-{suffix}"
    marker_a = f"qa{uuid.uuid4().hex[:18]}"
    marker_b = f"qb{uuid.uuid4().hex[:18]}"
    owners = [owner_a, owner_b]
    state_path = Path(args.state_file)

    try:
        store = MemoryStore(str(database_path))
        await store.upsert(
            owner_id=owner_a,
            scope="personal",
            kind="acceptance",
            key=f"marker-{marker_a}",
            value=f"acceptance value {marker_a}",
            source_turn_id="acceptance:seed:a",
            evidence=f"synthetic acceptance evidence {marker_a}",
        )
        await store.upsert(
            owner_id=owner_b,
            scope="personal",
            kind="acceptance",
            key=f"marker-{marker_b}",
            value=f"acceptance value {marker_b}",
            source_turn_id="acceptance:seed:b",
            evidence=f"synthetic acceptance evidence {marker_b}",
        )
        fact_a = await store.get_active_by_key(
            owner_id=owner_a,
            scope="personal",
            key=f"marker-{marker_a}",
        )
        fact_b = await store.get_active_by_key(
            owner_id=owner_b,
            scope="personal",
            key=f"marker-{marker_b}",
        )
        if fact_a is None or fact_b is None:
            raise MatrixError("failed to create acceptance facts")

        state = {
            "version": 1,
            "database_path": str(database_path),
            "service": args.service,
            "service_pid_before": _service_main_pid(args.service),
            "owner_a": owner_a,
            "owner_b": owner_b,
            "marker_a": marker_a,
            "marker_b": marker_b,
            "fact_a_id": fact_a.id,
            "fact_a_revision": fact_a.revision,
            "fact_b_id": fact_b.id,
            "fact_b_revision": fact_b.revision,
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "phase": "prepare",
            "state_file": str(state_path),
            "service_pid_before": state["service_pid_before"],
            "database_path": str(database_path),
            "owners_are_synthetic": True,
            "facts_seeded": 2,
            "next_step": f"restart {args.service}, then run --phase resume",
        }
    except Exception:
        _purge_acceptance_owners(database_path, owners)
        state_path.unlink(missing_ok=True)
        raise


def _validated_state(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    state_path = Path(args.state_file)
    if not state_path.is_file():
        raise MatrixError(f"acceptance state file missing: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1:
        raise MatrixError("invalid durable acceptance state")
    owner_a = str(state.get("owner_a") or "")
    owner_b = str(state.get("owner_b") or "")
    if not owner_a.startswith("veetee-accept-a-"):
        raise MatrixError("invalid synthetic owner A in state")
    if not owner_b.startswith("veetee-accept-b-"):
        raise MatrixError("invalid synthetic owner B in state")
    config, database_path = _load_memory_config(args)
    _ = config
    if Path(str(state.get("database_path") or "")).resolve() != database_path.resolve():
        raise MatrixError("acceptance database path changed between phases")
    return state, database_path


async def resume(args: argparse.Namespace) -> dict[str, Any]:
    state, database_path = _validated_state(args)
    http_base = args.http_base.rstrip("/")
    await wait_for_http_ready(http_base, timeout_seconds=30.0)
    management_token = load_management_token(args)

    owner_a = str(state["owner_a"])
    owner_b = str(state["owner_b"])
    marker_a = str(state["marker_a"])
    marker_b = str(state["marker_b"])
    owners = [owner_a, owner_b]
    state_path = Path(args.state_file)
    device_a = None
    device_b = None
    cleanup_errors: list[str] = []
    report: dict[str, Any] = {
        "phase": "resume",
        "database_path": str(database_path),
        "durable_enabled": True,
        "owners_are_synthetic": True,
        "checks": {},
    }

    try:
        active = await _active_sessions(http_base, management_token)
        if active:
            raise MatrixError(
                f"refusing acceptance resume while active_sessions={active}"
            )

        before_pid = int(state.get("service_pid_before") or 0)
        after_pid = _service_main_pid(str(state.get("service") or args.service))
        if before_pid <= 0 or after_pid <= 0 or before_pid == after_pid:
            raise MatrixError(
                f"service restart not proven (before={before_pid}, after={after_pid})"
            )
        report["checks"]["service_restart"] = {
            "pid_before": before_pid,
            "pid_after": after_pid,
            "changed": True,
        }

        reopened = MemoryStore(str(database_path))
        fact_a = await reopened.get_active_by_id(
            owner_id=owner_a,
            scope="personal",
            fact_id=int(state["fact_a_id"]),
        )
        fact_b = await reopened.get_active_by_id(
            owner_id=owner_b,
            scope="personal",
            fact_id=int(state["fact_b_id"]),
        )
        if (
            fact_a is None
            or fact_b is None
            or marker_a not in fact_a.value
            or marker_b not in fact_b.value
        ):
            raise MatrixError("synthetic facts did not persist across service restart")
        report["checks"]["persistence"] = True

        cross = await reopened.get_active_by_id(
            owner_id=owner_b,
            scope="personal",
            fact_id=fact_a.id,
        )
        if cross is not None:
            raise MatrixError("owner B can read owner A fact by id")
        report["checks"]["store_owner_isolation"] = True

        updated = await reopened.update_by_id(
            owner_id=owner_a,
            scope="personal",
            fact_id=fact_a.id,
            expected_revision=fact_a.revision,
            value=f"acceptance updated {marker_a}",
            source_turn_id="acceptance:update:a",
            evidence=f"synthetic acceptance update {marker_a}",
        )
        if updated is None or updated.revision != fact_a.revision + 1:
            raise MatrixError("optimistic memory update did not advance revision")
        stale = await reopened.update_by_id(
            owner_id=owner_a,
            scope="personal",
            fact_id=fact_a.id,
            expected_revision=fact_a.revision,
            value="stale write must not apply",
            source_turn_id="acceptance:stale:a",
            evidence="synthetic stale write",
        )
        if stale is not None:
            raise MatrixError("stale revision update was accepted")
        report["checks"]["revision_conflict_rejected"] = True

        device_a = await pair_probe_device(
            http_base,
            management_token,
            ordinal=101,
            owner_id=owner_a,
            name="Durable Acceptance A",
        )
        device_b = await pair_probe_device(
            http_base,
            management_token,
            ordinal=102,
            owner_id=owner_b,
            name="Durable Acceptance B",
        )
        devices = await request_json(
            "GET",
            f"{http_base}/api/devices",
            token=management_token,
        )
        bound = {
            (row.get("device_id"), row.get("client_id")): row.get("owner_id")
            for row in (devices.get("devices") or [])
            if isinstance(row, dict)
        }
        if bound.get((device_a.device_id, device_a.client_id)) != owner_a:
            raise MatrixError("device A owner binding mismatch")
        if bound.get((device_b.device_id, device_b.client_id)) != owner_b:
            raise MatrixError("device B owner binding mismatch")
        report["checks"]["paired_device_owner_binding"] = True

        probe_a = await run_context_probe(
            uri=args.uri,
            http_base=http_base,
            management_token=management_token,
            device=device_a,
            query=marker_a,
            timeout=args.timeout,
        )
        probe_b = await run_context_probe(
            uri=args.uri,
            http_base=http_base,
            management_token=management_token,
            device=device_b,
            query=marker_a,
            timeout=args.timeout,
        )
        expected_a_id = f"durable:{updated.id}"
        if not probe_a["protocol_completed"] or probe_a["outcome"] != "completed":
            raise MatrixError(f"owner A runtime probe failed: {probe_a}")
        if expected_a_id not in probe_a["durable_ids"]:
            raise MatrixError(
                f"owner A did not retrieve its durable fact id: {probe_a}"
            )
        if not probe_b["protocol_completed"] or probe_b["outcome"] != "completed":
            raise MatrixError(f"owner B runtime probe failed: {probe_b}")
        if expected_a_id in probe_b["durable_ids"]:
            raise MatrixError(
                f"owner B received owner A durable fact id: {probe_b}"
            )
        report["checks"]["runtime_owner_isolation"] = {
            "owner_a": probe_a,
            "owner_b_same_query": probe_b,
        }

        forgotten = await reopened.tombstone_by_id(
            owner_id=owner_a,
            scope="personal",
            fact_id=updated.id,
            expected_revision=updated.revision,
        )
        if not forgotten:
            raise MatrixError("forget/tombstone did not commit")
        if await reopened.get_active_by_id(
            owner_id=owner_a,
            scope="personal",
            fact_id=updated.id,
        ) is not None:
            raise MatrixError("forgotten fact is still active in durable store")

        probe_after_forget = await run_context_probe(
            uri=args.uri,
            http_base=http_base,
            management_token=management_token,
            device=device_a,
            query=marker_a,
            timeout=args.timeout,
        )
        if expected_a_id in probe_after_forget["durable_ids"]:
            raise MatrixError(
                "forgotten fact id was retrieved by a fresh authenticated session"
            )
        report["checks"]["forget_barrier"] = probe_after_forget
        report["passed"] = True
        return report
    finally:
        for device in (device_a, device_b):
            if device is None:
                continue
            error = await revoke_probe_device(
                http_base,
                management_token,
                device,
            )
            if error:
                cleanup_errors.append(error)
        try:
            _purge_acceptance_owners(database_path, owners)
        except Exception as exc:
            cleanup_errors.append(
                f"memory cleanup {type(exc).__name__}: {exc}"
            )
        state_path.unlink(missing_ok=True)
        report["cleanup_errors"] = cleanup_errors


def cleanup(args: argparse.Namespace) -> dict[str, Any]:
    state, database_path = _validated_state(args)
    owners = [str(state["owner_a"]), str(state["owner_b"])]
    _purge_acceptance_owners(database_path, owners)
    Path(args.state_file).unlink(missing_ok=True)
    return {
        "phase": "cleanup",
        "cleaned": True,
        "owners_are_synthetic": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("prepare", "resume", "cleanup"),
        required=True,
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--uri", default="ws://127.0.0.1:8000/")
    parser.add_argument("--http-base", default="http://127.0.0.1:8003")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--service", default="veetee")
    parser.add_argument(
        "--state-file",
        default="docs/benchmarks/durable-owner-acceptance-state.json",
    )
    parser.add_argument("--management-token-env", default="VEETEE_MANAGEMENT_TOKEN")
    parser.add_argument("--management-env-file", default=".env")
    parser.add_argument(
        "--artifact",
        default="docs/benchmarks/durable-owner-acceptance.json",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.phase == "prepare":
            report = asyncio.run(prepare(args))
        elif args.phase == "resume":
            report = asyncio.run(resume(args))
        else:
            report = cleanup(args)
    except MatrixError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    if args.phase == "resume":
        artifact = Path(args.artifact)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["artifact"] = str(artifact)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.phase != "resume":
        return 0
    return 0 if report.get("passed") and not report.get("cleanup_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
