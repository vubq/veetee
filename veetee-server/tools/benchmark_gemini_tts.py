#!/usr/bin/env python3
"""Run a credential-safe native Gemini TTS streaming smoke benchmark."""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts")
TEXT = "Xin chào, đây là bài kiểm tra âm thanh tiếng Việt của Veetee."


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("GEMINI_API_KEY_") and value.strip():
            values[name] = value.strip().strip('"').strip("'")
    return dict(sorted(values.items()))


def request_tts(model: str, key: str, stream: bool) -> dict[str, Any]:
    endpoint = "streamGenerateContent?alt=sse" if stream else "generateContent"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{endpoint}"
    body = {
        "contents": [{"parts": [{"text": TEXT}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
        },
    }
    request = urllib.request.Request(
        f"{url}?key={key}" if "?" not in url else f"{url}&key={key}",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    audio_bytes = 0
    events = 0
    first_audio_ms: float | None = None
    status = None
    error_code = None
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            status = response.status
            if stream:
                for raw_line in response:
                    line = raw_line.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    events += 1
                    for part in event.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                        inline = part.get("inlineData") or part.get("inline_data")
                        if not inline or not inline.get("data"):
                            continue
                        audio_bytes += len(base64.b64decode(inline["data"]))
                        first_audio_ms = first_audio_ms or (time.perf_counter() - started) * 1000
            else:
                event = json.loads(response.read())
                events = 1
                for part in event.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        audio_bytes += len(base64.b64decode(inline["data"]))
                first_audio_ms = (time.perf_counter() - started) * 1000 if audio_bytes else None
    except urllib.error.HTTPError as exc:
        status = exc.code
        error_code = f"http_{exc.code}"
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        error_code = type(exc).__name__
    return {
        "model": model,
        "stream": stream,
        "status": status,
        "events": events,
        "audio_bytes": audio_bytes,
        "first_audio_ms": round(first_audio_ms, 1) if first_audio_ms is not None else None,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / ".secrets" / "gemini.env",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    keys = load_env(args.env_file)
    if len(keys) < 2:
        parser.error("at least two non-empty GEMINI_API_KEY_N values are required")

    results: list[dict[str, Any]] = []
    for slot, key in keys.items():
        results.append({"key_slot": slot, **request_tts(MODELS[0], key, stream=True)})
    # One buffered fallback smoke request verifies the secondary model mapping.
    first_key = next(iter(keys.values()))
    results.append({"key_slot": "fallback_slot_1", **request_tts(MODELS[1], first_key, stream=False)})

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "key_slots": list(keys),
        "models": list(MODELS),
        "results": results,
        "secret_values_written": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result["status"] == 200 and result["audio_bytes"] > 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
