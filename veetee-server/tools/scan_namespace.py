#!/usr/bin/env python3
"""Reject forbidden upstream identifiers from Veetee product files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(r"xiaozhi", re.IGNORECASE)
DEFAULT_ROOTS = (
    "veetee-server/server",
    "veetee-server/contracts",
    "veetee-server/deploy",
    "veetee-server/web/src",
    "veetee-firmware/main",
)
SKIP_PARTS = {"references", "node_modules", ".git", "build", "dist", ".venv"}
PRODUCT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".vue", ".json", ".jsonc", ".yaml", ".yml",
    ".toml", ".sql",
}
SELF = Path(__file__).resolve()


def files_to_scan(scan_all: bool) -> tuple[list[Path], list[str]]:
    roots = (ROOT,) if scan_all else tuple(ROOT / item for item in DEFAULT_ROOTS)
    files: list[Path] = []
    forbidden_paths: list[str] = []
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(ROOT)
            if any(part in SKIP_PARTS for part in relative.parts):
                continue
            if path.resolve() == SELF:
                continue
            if FORBIDDEN.search(relative.as_posix()):
                forbidden_paths.append(relative.as_posix())
            if not path.is_file() or path.suffix.lower() not in PRODUCT_SUFFIXES:
                continue
            files.append(path)
    return sorted(set(files)), sorted(set(forbidden_paths))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="scan all non-reference repository files")
    args = parser.parse_args()

    files, forbidden_paths = files_to_scan(args.all)
    violations: list[tuple[str, int]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                violations.append((path.relative_to(ROOT).as_posix(), line_number))

    if forbidden_paths or violations:
        for path in forbidden_paths:
            print(f"{path}: forbidden identifier in path")
        for path, line_number in violations:
            print(f"{path}:{line_number}: forbidden identifier in content")
        return 1

    mode = "all files" if args.all else "product source/config"
    print(f"namespace scan passed ({mode}; references excluded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
