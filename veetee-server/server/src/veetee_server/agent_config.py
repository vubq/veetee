"""Stable agent configuration serialization shared by runtime and persistence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

AGENT_CONFIG_SCHEMA_VERSION = "v1"


def canonical_agent_checksum(config: Mapping[str, Any]) -> str:
    """Returns a stable SHA-256 checksum for an agent configuration."""
    encoded = json.dumps(
        config, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
