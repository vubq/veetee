#!/usr/bin/env python3
"""Create the ignored voice-server environment from local trusted services."""

from __future__ import annotations

import ast
import os
import tempfile
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parent.parent
VOICE_EXAMPLE = SERVER_ROOT / "apps/voice-server/.env.example"
VOICE_ENV = SERVER_ROOT / "apps/voice-server/.env"
MANAGER_ENV = SERVER_ROOT / "apps/manager-api/.env"
CLIPROXY_CONFIG = Path(
    os.environ.get(
        "VEETEE_CLIPROXY_CONFIG_PATH",
        Path.home() / "cliproxyapi/config.yaml",
    )
)


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


def first_yaml_list_item(path: Path, key: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    section_indent: int | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        if section_indent is None:
            if stripped == f"{key}:":
                section_indent = indent
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if indent <= section_indent:
            break
        if not stripped.startswith("-"):
            continue
        scalar = stripped[1:].strip()
        if not scalar:
            continue
        if scalar[0] in {'"', "'"}:
            try:
                value = ast.literal_eval(scalar)
            except (SyntaxError, ValueError) as error:
                raise RuntimeError(f"{key} contains an invalid quoted value") from error
        else:
            value = scalar.split(" #", 1)[0].strip()
        if isinstance(value, str) and value:
            return value
    raise RuntimeError(f"{key} does not contain a usable value")


def active_cliproxy_key() -> str:
    return first_yaml_list_item(CLIPROXY_CONFIG, "api-keys")


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
    current_voice = parse_environment(VOICE_ENV) if VOICE_ENV.is_file() else {}
    manager_token = manager.get("VEETEE_INTERNAL_SERVICE_TOKEN", "")
    if len(manager_token) < 24:
        raise RuntimeError("Manager internal service token is missing or invalid")
    replacements = {
        "OPENBLAS_NUM_THREADS": "1",
        "VEETEE_HOST": "0.0.0.0",
        "VEETEE_RELOAD": "false",
        "VEETEE_MANAGER_API_URL": "http://127.0.0.1:8001",
        "VEETEE_MANAGER_INTERNAL_TOKEN": manager_token,
        "VEETEE_LAB_ALLOWED_ORIGINS": manager.get(
            "VEETEE_MANAGER_CORS_ORIGIN",
            "http://127.0.0.1:8081,http://localhost:8081",
        ),
        "VEETEE_CLIPROXY_BASE_URL": "http://127.0.0.1:8317/v1",
        "VEETEE_CLIPROXY_API_KEY": active_cliproxy_key(),
        "VEETEE_CLIPROXY_MODEL": "gpt-5.6-terra",
    }
    cookie_file = os.environ.get("VEETEE_MEDIA_YOUTUBE_COOKIE_FILE", "").strip()
    if not cookie_file:
        cookie_file = current_voice.get("VEETEE_MEDIA_YOUTUBE_COOKIE_FILE", "").strip()
    if cookie_file:
        replacements["VEETEE_MEDIA_YOUTUBE_COOKIE_FILE"] = cookie_file
    atomic_write_private(VOICE_ENV, render_environment(replacements))
    print(f"Configured ignored voice environment at {VOICE_ENV} (secrets redacted)")


if __name__ == "__main__":
    main()
