#!/usr/bin/env python3
"""A/B two LLM models through the SAME quota router/ledger and persona.

Compares default model vs llm.extra_models on identical questions:
time-to-first-event, total time, lifecycle, tool calls, bracket leaks,
answer text. All requests share quota accounting, so the test itself is
quota-safe. Uses real Groq quota (small N by default).

Exit codes: 0 ran to completion, 2 BLOCKED (no models/keys).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

QUESTIONS = [
    ("chat", "Xin chào bạn."),
    ("clock", "Mấy giờ rồi?"),
    ("garbled", "Mới giơ rồi."),
]


async def run_turn(engine, text: str, timeout: float) -> dict:
    """One text turn; returns timing + event summary (no audio involved)."""
    from core.turn_events import (
        CompletedEvent, ControlEvent, FailedEvent, SpeechSegmentEvent,
        ToolCallReadyEvent,
    )
    started = time.monotonic()
    first_at = None
    control = None
    tools: list[str] = []
    speech: list[str] = []
    failed = None
    try:
        async for event in engine.stream_turn(
                [{"role": "user", "content": text}], detect_end_intent=True):
            if first_at is None:
                first_at = time.monotonic()
            if isinstance(event, ControlEvent):
                control = f"{event.intent}/{event.lifecycle}"
            elif isinstance(event, ToolCallReadyEvent):
                tools.append(event.name)
            elif isinstance(event, SpeechSegmentEvent):
                speech.append(event.text)
            elif isinstance(event, FailedEvent):
                failed = str(event)[:120]
                break
            if time.monotonic() - started > timeout:
                failed = "client-timeout"
                break
    except Exception as exc:  # noqa: BLE001 - report, don't crash matrix
        failed = f"{type(exc).__name__}: {str(exc)[:100]}"
    total = time.monotonic() - started
    full_speech = " ".join(speech)
    return {
        "ttft_s": round(first_at - started, 3) if first_at else None,
        "total_s": round(total, 3),
        "control": control,
        "tools": tools,
        "speech_chars": len(full_speech),
        "speech": full_speech[:160],
        "bracket_leak": ("[" in full_speech and "]" in full_speech),
        "failed": failed,
    }


async def main_async(args) -> int:
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from config.settings import load_settings
    from core.providers.llm.groq_direct import (
        build_engine_from_config, engine_for_model)

    config = load_settings(args.config) if args.config else load_settings()
    models = [config.llm.model] + [m for m in config.llm.extra_models
                                   if m != config.llm.model]
    if args.models:
        wanted = set(args.models)
        models = [m for m in models if m in wanted]
    if not models:
        print("BLOCKED: no models selected", flush=True)
        return 2
    try:
        base = build_engine_from_config(
            config.llm, server_dir=os.path.join(
                os.path.dirname(__file__), ".."))
    except ValueError as exc:
        print(f"BLOCKED: {exc}", flush=True)
        return 2
    engines = {models[0]: base}
    for model in models[1:]:
        engines[model] = engine_for_model(base, model)

    report = {"models": {}, "questions": [q[0] for q in QUESTIONS]}
    for model, engine in engines.items():
        print(f"=== model: {model} ===", flush=True)
        rows = {}
        for kind, text in QUESTIONS:
            result = await run_turn(engine, text, args.timeout)
            rows[kind] = result
            print(f"  {kind}: ttft={result['ttft_s']} total={result['total_s']} "
                  f"control={result['control']} tools={result['tools']} "
                  f"leak={result['bracket_leak']} failed={result['failed']}",
                  flush=True)
            print(f"    speech: {result['speech']}", flush=True)
        report["models"][model] = rows
        await asyncio.sleep(2)
    for engine in engines.values():
        await engine.close()
    print("=== AB REPORT ===", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=1), flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
        print(f"saved: {args.out}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="")
    parser.add_argument("--models", nargs="*", default=[],
                        help="subset of models to test")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
