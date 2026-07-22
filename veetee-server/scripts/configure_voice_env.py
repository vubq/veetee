#!/usr/bin/env python3
"""Create the ignored voice-server environment from local trusted services."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parent.parent
VOICE_EXAMPLE = SERVER_ROOT / "apps/voice-server/.env.example"
VOICE_ENV = SERVER_ROOT / "apps/voice-server/.env"
MANAGER_ENV = SERVER_ROOT / "apps/manager-api/.env"
NINE_ROUTER_DB = Path.home() / ".9router/db/data.sqlite"


def parse_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name] = value
    return values


def active_nine_router_key() -> str:
    connection = sqlite3.connect(f"file:{NINE_ROUTER_DB}?mode=ro", uri=True)
    try:
        row = connection.execute(
            'SELECT "key" FROM "apiKeys" WHERE "isActive" = 1 '
            'ORDER BY "createdAt" DESC LIMIT 1'
        ).fetchone()
    finally:
        connection.close()
    if not row or not isinstance(row[0], str) or not row[0]:
        raise RuntimeError("9Router does not have an active local API key")
    return row[0]


def render_environment(replacements: dict[str, str]) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for raw_line in VOICE_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.lstrip().startswith("#") or "=" not in raw_line:
            output.append(raw_line)
            continue
        name = raw_line.split("=", 1)[0]
        if name in replacements:
            output.append(f"{name}={replacements[name]}")
            seen.add(name)
        else:
            output.append(raw_line)
    for name, value in replacements.items():
        if name not in seen:
            output.append(f"{name}={value}")
    return "\n".join(output) + "\n"


def atomic_write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> None:
    manager = parse_environment(MANAGER_ENV)
    manager_token = manager.get("VEETEE_INTERNAL_SERVICE_TOKEN", "")
    if len(manager_token) < 24:
        raise RuntimeError("Manager internal service token is missing or invalid")
    replacements = {
        "VEETEE_HOST": "0.0.0.0",
        "VEETEE_RELOAD": "false",
        "VEETEE_MANAGER_API_URL": "http://127.0.0.1:8001",
        "VEETEE_MANAGER_INTERNAL_TOKEN": manager_token,
        "VEETEE_LAB_ALLOWED_ORIGINS": manager.get(
            "VEETEE_MANAGER_CORS_ORIGIN",
            "http://127.0.0.1:8081,http://localhost:8081",
        ),
        "VEETEE_9ROUTER_API_KEY": active_nine_router_key(),
    }
    atomic_write_private(VOICE_ENV, render_environment(replacements))
    print(f"Configured ignored voice environment at {VOICE_ENV} (secrets redacted)")


if __name__ == "__main__":
    main()
