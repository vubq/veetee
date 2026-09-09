#!/usr/bin/env python3
"""M0.1 snapshot: source/config/persona/schema/route/hardware/deps fingerprints.

Writes a JSON snapshot without secrets or private persona text (only hashes
and token estimates). Old artifacts are never reassigned to new sources.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(cmd, cwd=cwd, text=True, stderr=subprocess.DEVNULL,
                                       timeout=2).strip()
    except Exception:
        return "unknown"


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    server = Path(__file__).resolve().parents[1]
    head = _run(["git", "rev-parse", "HEAD"], repo)
    dirty = _run(["git", "status", "--short"], repo)
    dirty_fp = hashlib.sha256(dirty.encode()).hexdigest()[:16] if dirty != "unknown" else "unknown"

    # Config fingerprint (redacted) without importing server deps.
    config_fp = "unknown"
    try:
        sys.path.insert(0, str(server))
        from config.settings import load_settings
        from core.turn_metrics import config_fingerprint
        cfg = load_settings(str(server / "config.yaml"))
        config_fp = config_fingerprint(cfg)
        context_tokens = cfg.latency.context_max_tokens
        chars_per_token = cfg.latency.context_chars_per_token
        llm_model = cfg.llm.model
        llm_url = cfg.llm.base_url
    except Exception as exc:
        context_tokens = chars_per_token = llm_model = llm_url = f"unreadable:{type(exc).__name__}"

    persona_path = server / "data" / "base-prompt.txt"
    persona_bytes = persona_hash = "missing"
    persona_tokens_est = 0
    try:
        raw = persona_path.read_bytes()
        persona_bytes = len(raw)
        persona_hash = hashlib.sha256(raw).hexdigest()[:16]
        persona_tokens_est = int(len(raw) / 4 * 1.25) + 1
    except OSError:
        pass

    # Schema/catalog hash without importing heavy deps.
    schema_hash = "unknown"
    try:
        sys.path.insert(0, str(server))
        from core.tools.builtin.time_tool import time_descriptor
        from core.tools.builtin.calculator import calculator_descriptor
        from core.tools.registry import ToolRegistry
        registry = ToolRegistry([time_descriptor(), calculator_descriptor()])
        schema_hash = registry.catalog_fingerprint()
    except Exception:
        pass

    snapshot = {
        "taken_at": datetime.now(timezone.utc).isoformat(),
        "head": head,
        "dirty_fingerprint": dirty_fp,
        "dirty_short": dirty[:2000] if dirty not in ("", "unknown") else dirty,
        "config_fingerprint_redacted": config_fp,
        "context_max_tokens": context_tokens,
        "context_chars_per_token": chars_per_token,
        "persona_bytes": persona_bytes,
        "persona_sha16": persona_hash,
        "persona_tokens_est": persona_tokens_est,
        "schema_catalog_hash": schema_hash,
        "route_model_configured": llm_model,
        "route_base_url": llm_url,
        "route_observed": "unknown (record from live /diagnostics, never infer from headers)",
        "hardware": {"platform": platform.platform(), "machine": platform.machine(),
                     "processor": platform.processor()},
        "python": platform.python_version(),
        "deps": "record `pip freeze` separately; versions not snapshotted here",
        "process_start": "record at server start; this script does not start engines",
    }
    out_dir = server / "benchmark-artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"m0-snapshot-{stamp}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(f"snapshot={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
