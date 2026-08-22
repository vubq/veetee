"""Namespace scanner allowlist remains narrow and explicit."""

import importlib.util
from pathlib import Path
from types import ModuleType


def _scanner() -> ModuleType:
    path = Path(__file__).parents[2] / "tools" / "scan_namespace.py"
    spec = importlib.util.spec_from_file_location("veetee_namespace_scan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reference_harness_allowlist_is_exact() -> None:
    scanner = _scanner()
    harness = "veetee-server/server/src/veetee_server/digital_human_harness/app.py"
    upstream_name = "xiao" + "zhi"
    reference = f'        / "references/{upstream_name}-esp32-server/main/digital-human"'

    assert scanner.is_allowed_content(harness, reference)
    assert not scanner.is_allowed_content(harness, f'route = "/{upstream_name}/ota/"')
    assert not scanner.is_allowed_content("veetee-server/server/src/other.py", reference)
