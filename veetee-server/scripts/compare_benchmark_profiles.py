#!/usr/bin/env python3
"""Compare VeeTee benchmark summaries without inventing a winner.

This utility reports deltas for end-to-end and stage percentiles. It does not
change runtime configuration and it intentionally does not select a profile;
endpoint quality (false-endpoint/CER/WER) and hardware acceptance remain
separate gates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


KEY_METRICS = (
    "p50_ms",
    "p90_ms",
    "p95_ms",
    "max_ms",
    "success_rate",
)

STAGE_ORDER = (
    "speech_end_to_stt_final_ms",
    "stt_final_to_first_clause_ms",
    "first_clause_to_first_binary_received_ms",
    "first_binary_to_first_voiced_pcm_received_ms",
    "speech_end_to_first_binary_received_ms",
    "speech_end_to_first_voiced_pcm_received_ms",
)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _delta(value: Any, baseline: Any) -> Optional[float]:
    current = _number(value)
    base = _number(baseline)
    if current is None or base is None:
        return None
    return round(current - base, 3)


def compare_summaries(
    baseline: Dict[str, Any],
    candidates: Iterable[tuple[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for label, summary in candidates:
        row: Dict[str, Any] = {
            "label": label,
            "requested_samples": summary.get("requested_samples"),
            "successful_samples": summary.get("successful_samples"),
            "sla_status": summary.get("sla_status"),
        }
        for key in KEY_METRICS:
            row[key] = summary.get(key)
            row[f"{key}_delta"] = _delta(summary.get(key), baseline.get(key))

        stage_rows: Dict[str, Any] = {}
        baseline_stages = baseline.get("stage_latency_ms") or {}
        stages = summary.get("stage_latency_ms") or {}
        for stage in STAGE_ORDER:
            current = stages.get(stage) or {}
            base = baseline_stages.get(stage) or {}
            stage_rows[stage] = {
                "samples": current.get("samples"),
                "p50": current.get("p50"),
                "p50_delta": _delta(current.get("p50"), base.get("p50")),
                "p95": current.get("p95"),
                "p95_delta": _delta(current.get("p95"), base.get("p95")),
                "max": current.get("max"),
            }
        row["stage_latency_ms"] = stage_rows
        rows.append(row)
    return {
        "baseline": {
            key: baseline.get(key)
            for key in ("requested_samples", "successful_samples", "sla_status", *KEY_METRICS)
        },
        "comparisons": rows,
        "decision_note": (
            "Latency deltas are descriptive only. Do not select an endpoint/VAD "
            "profile without false-endpoint and CER/WER gates, and do not treat "
            "server receive time as physical speaker acceptance."
        ),
    }


def _load(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", help="baseline *.summary.json")
    parser.add_argument("candidates", nargs="+", help="candidate *.summary.json")
    parser.add_argument("--output", help="optional output JSON")
    args = parser.parse_args()

    baseline = _load(args.baseline)
    candidates = [(Path(path).stem, _load(path)) for path in args.candidates]
    result = compare_summaries(baseline, candidates)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
