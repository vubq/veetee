#!/usr/bin/env python3
"""Run the M0.3 harmless streaming benchmark against local OmniRoute."""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


MODELS = ("groq/openai/gpt-oss-120b", "groq/qwen/qwen3.6-27b")
MODEL_OPTIONS = {
    "groq/openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "groq/qwen/qwen3.6-27b": {"reasoning_effort": "none"},
}
SYSTEM_PROMPT = (
    "Bạn là trợ lý giọng nói Veetee. Trả lời bằng tiếng Việt tự nhiên, ngắn gọn, "
    "không bịa thông tin và chỉ gọi công cụ khi người dùng yêu cầu dữ liệu cần công cụ."
)
CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "vietnamese_conversation",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Giải thích trong hai câu vì sao trời có cầu vồng."},
        ],
    },
    {
        "id": "instruction_following",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Nêu đúng ba bước tiết kiệm điện, mỗi bước không quá tám từ.",
            },
        ],
    },
    {
        "id": "multi_turn_context",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Tên chú mèo của tôi là Mây."},
            {"role": "assistant", "content": "Tôi đã nhớ tên chú mèo của bạn là Mây."},
            {"role": "user", "content": "Chú mèo của tôi tên gì? Chỉ trả lời tên."},
        ],
    },
    {
        "id": "tool_call",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Cho tôi biết thời tiết ở Đà Nẵng hiện tại."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Lấy thời tiết hiện tại theo thành phố.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
        "tool_choice": "auto",
    },
)


def stream_case(
    base_url: str,
    api_key: str,
    model: str,
    case: dict[str, Any],
    run_tag: str,
) -> dict[str, Any]:
    parsed = urlsplit(base_url)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=60)
    case_payload = copy.deepcopy(case)
    case_payload["messages"][0]["content"] += f" Mã benchmark: {run_tag}."
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0,
        "max_tokens": 512,
        **MODEL_OPTIONS[model],
        **case_payload,
    }
    payload.pop("id", None)
    started = time.perf_counter()
    connection.request(
        "POST",
        f"{parsed.path.rstrip('/')}/chat/completions",
        body=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    headers_at = time.perf_counter()
    content_parts: list[str] = []
    tool_deltas: list[dict[str, Any]] = []
    usage: dict[str, Any] | None = None
    first_event_ms: float | None = None
    first_output_ms: float | None = None
    error_body = ""

    if response.status != 200:
        error_body = response.read(2048).decode(errors="replace")
    else:
        while line := response.readline():
            if not line.startswith(b"data: "):
                continue
            raw = line[6:].strip()
            if raw == b"[DONE]":
                break
            event_at = time.perf_counter()
            first_event_ms = first_event_ms or (event_at - started) * 1000
            event = json.loads(raw)
            usage = event.get("usage") or usage
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            tools = delta.get("tool_calls") or []
            if content:
                first_output_ms = first_output_ms or (event_at - started) * 1000
                content_parts.append(content)
            if tools:
                first_output_ms = first_output_ms or (event_at - started) * 1000
                tool_deltas.extend(tools)

    finished = time.perf_counter()
    connection.close()
    return {
        "case": case["id"],
        "status": response.status,
        "headers_ms": round((headers_at - started) * 1000, 1),
        "first_event_ms": round(first_event_ms, 1) if first_event_ms is not None else None,
        "first_output_ms": round(first_output_ms, 1) if first_output_ms is not None else None,
        "total_ms": round((finished - started) * 1000, 1),
        "content": "".join(content_parts),
        "tool_call_deltas": tool_deltas,
        "usage": usage,
        "error": error_body[:500] if error_body else None,
    }


def cancellation_case(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    case = {
        "id": "cancellation",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Kể một câu chuyện dài về một khu rừng tưởng tượng."},
        ],
    }
    parsed = urlsplit(base_url)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=60)
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0,
        "max_tokens": 1024,
        **MODEL_OPTIONS[model],
        **case,
    }
    payload.pop("id", None)
    started = time.perf_counter()
    connection.request(
        "POST",
        f"{parsed.path.rstrip('/')}/chat/completions",
        body=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    received_output = False
    if response.status == 200:
        while line := response.readline():
            if not line.startswith(b"data: ") or line[6:].strip() == b"[DONE]":
                continue
            event = json.loads(line[6:])
            choices = event.get("choices") or []
            if choices and (choices[0].get("delta") or {}).get("content"):
                received_output = True
                break
    connection.close()
    return {
        "case": "cancellation",
        "status": response.status,
        "received_output_before_close": received_output,
        "closed_after_ms": round((time.perf_counter() - started) * 1000, 1),
        "scope": "client transport closed; upstream compute cancellation not observable from this API",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:20128/v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    api_key = os.environ.get("OMNIROUTE_API_KEY")
    if not api_key:
        parser.error("OMNIROUTE_API_KEY is required")

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "models": {},
    }
    for model in MODELS:
        report["models"][model] = {
            "reasoning_effort": MODEL_OPTIONS[model]["reasoning_effort"],
            "cases": [
                {
                    "run": run,
                    **stream_case(
                        args.base_url,
                        api_key,
                        model,
                        case,
                        f"{report['generated_at']}-{model}-{case['id']}-{run}",
                    ),
                }
                for run in range(1, args.runs + 1)
                for case in CASES
            ],
            "cancellation": cancellation_case(args.base_url, api_key, model),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(case["status"] == 200 for model in report["models"].values() for case in model["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
