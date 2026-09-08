from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional


logger = logging.getLogger("TurnMetrics")

RUN_ID = os.getenv("VEETEE_RUN_ID") or uuid.uuid4().hex
_current_trace: contextvars.ContextVar[Optional["TurnTrace"]] = contextvars.ContextVar(
    "veetee_turn_trace",
    default=None,
)


def _repo_commit() -> str:
    repo = Path(__file__).resolve().parents[2]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        ).strip()
    except Exception:
        return "unknown"


COMMIT = _repo_commit()


def config_fingerprint(config: Any) -> str:
    """Return a stable, non-secret config fingerprint."""
    if is_dataclass(config):
        value = asdict(config)
    elif isinstance(config, dict):
        value = dict(config)
    else:
        value = str(config)

    def scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            result = {}
            for key, item in obj.items():
                lowered = str(key).lower()
                if any(secret in lowered for secret in ("key", "token", "password", "secret")):
                    result[key] = "<redacted>"
                else:
                    result[key] = scrub(item)
            return result
        if isinstance(obj, list):
            return [scrub(item) for item in obj]
        return obj

    encoded = json.dumps(scrub(value), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass
class MetricEvent:
    name: str
    at_ms: float
    fields: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnTrace:
    session_id: str
    turn_id: str
    capture_id: int
    source: str
    config_fingerprint: str
    run_id: str = RUN_ID
    commit: str = COMMIT
    started_perf: float = field(default_factory=time.perf_counter)
    events: list[MetricEvent] = field(default_factory=list)
    outcome: str = "running"

    def mark(self, name: str, **fields: Any) -> None:
        event = MetricEvent(
            name=name,
            at_ms=(time.perf_counter() - self.started_perf) * 1000.0,
            fields={key: value for key, value in fields.items() if value is not None},
        )
        self.events.append(event)
        logger.info(
            "turn_metric %s",
            json.dumps(
                {
                    "run_id": self.run_id,
                    "commit": self.commit,
                    "config": self.config_fingerprint,
                    "session_id": self.session_id,
                    "turn_id": self.turn_id,
                    "capture_id": self.capture_id,
                    "source": self.source,
                    "event": event.name,
                    "at_ms": round(event.at_ms, 3),
                    **event.fields,
                },
                ensure_ascii=False,
                default=str,
            ),
        )

    def finish(self, outcome: str, **fields: Any) -> None:
        self.outcome = outcome
        self.mark("turn_finish", outcome=outcome, **fields)

    def first(self, name: str) -> Optional[MetricEvent]:
        return next((event for event in self.events if event.name == name), None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "commit": self.commit,
            "config_fingerprint": self.config_fingerprint,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "capture_id": self.capture_id,
            "source": self.source,
            "outcome": self.outcome,
            "events": [
                {"name": event.name, "at_ms": event.at_ms, **event.fields}
                for event in self.events
            ],
        }


def summarize_trace(trace: TurnTrace) -> Dict[str, Any]:
    payload = trace.to_dict()
    finish = trace.first("turn_finish")
    if finish is not None:
        payload.update(finish.fields)
    first_binary = trace.first("first_ws_binary_sent")
    if first_binary is not None:
        payload["turn_start_to_first_ws_binary_ms"] = round(first_binary.at_ms, 3)
    return payload


class TurnTraceStore:
    """Bounded server-level trace retention that survives session disconnects."""

    def __init__(self, *, max_recent: int = 100):
        self.max_recent = max(1, int(max_recent))
        self.recent: list[TurnTrace] = []

    def add(self, trace: TurnTrace) -> None:
        self.recent.append(trace)
        if len(self.recent) > self.max_recent:
            del self.recent[:-self.max_recent]


class TurnMetricsRecorder:
    def __init__(
        self,
        session_id: str,
        config: Any,
        *,
        max_recent: int = 20,
        shared_store: Optional[TurnTraceStore] = None,
    ):
        self.session_id = session_id
        self.config_fingerprint = config_fingerprint(config)
        self.max_recent = max(1, int(max_recent))
        self.shared_store = shared_store
        self.recent: list[TurnTrace] = []
        self.capture_events: Dict[int, list[Dict[str, Any]]] = {}
        self._turn_counter = 0

    def record_capture_event(
        self,
        capture_id: int,
        name: str,
        *,
        event_perf: Optional[float] = None,
        **fields: Any,
    ) -> None:
        bucket = self.capture_events.setdefault(int(capture_id), [])
        bucket.append({"name": name, "perf": event_perf or time.perf_counter(), **fields})
        if len(self.capture_events) > 8:
            oldest = sorted(self.capture_events)[:-8]
            for key in oldest:
                self.capture_events.pop(key, None)

    def start_turn(self, capture_id: int, source: str) -> TurnTrace:
        self._turn_counter += 1
        trace = TurnTrace(
            session_id=self.session_id,
            turn_id=f"{self.session_id}:{self._turn_counter}",
            capture_id=int(capture_id),
            source=source,
            config_fingerprint=self.config_fingerprint,
        )
        capture = self.capture_events.get(int(capture_id), [])
        for item in capture:
            fields = {key: value for key, value in item.items() if key not in {"name", "perf"}}
            event = MetricEvent(
                name=item["name"],
                at_ms=(item["perf"] - trace.started_perf) * 1000.0,
                fields=fields,
            )
            trace.events.append(event)
        trace.mark("turn_start")
        self.recent.append(trace)
        if len(self.recent) > self.max_recent:
            del self.recent[:-self.max_recent]
        if self.shared_store is not None:
            self.shared_store.add(trace)
        return trace

    def latest_summary(self) -> Optional[Dict[str, Any]]:
        if not self.recent:
            return None
        return summarize_trace(self.recent[-1])


def activate_trace(trace: TurnTrace):
    return _current_trace.set(trace)


def reset_trace(token) -> None:
    _current_trace.reset(token)


def current_trace() -> Optional[TurnTrace]:
    return _current_trace.get()


def mark_current(name: str, **fields: Any) -> None:
    trace = current_trace()
    if trace is not None:
        trace.mark(name, **fields)
