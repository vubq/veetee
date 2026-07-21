from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from types import ModuleType


def ensure_sherpa_onnx_runtime() -> ModuleType:
    """Bridge sherpa-onnx's unversioned ORT lookup to the pip ORT wheel."""
    onnx_spec = importlib.util.find_spec("onnxruntime")
    sherpa_spec = importlib.util.find_spec("sherpa_onnx")
    if onnx_spec is None or onnx_spec.origin is None:
        raise RuntimeError("onnxruntime is not installed")
    if sherpa_spec is None or sherpa_spec.origin is None:
        raise RuntimeError("sherpa-onnx is not installed")

    site_packages = Path(sherpa_spec.origin).parents[1]
    runtime_dir = Path(onnx_spec.origin).parent / "capi"
    candidates = sorted(runtime_dir.glob("libonnxruntime.so.*"))
    if not candidates:
        raise RuntimeError("onnxruntime wheel does not contain libonnxruntime.so")

    sherpa_libs = site_packages / "sherpa_onnx.libs"
    sherpa_libs.mkdir(exist_ok=True)
    link = sherpa_libs / "libonnxruntime.so"
    target = candidates[-1]
    if not link.exists() or link.resolve() != target.resolve():
        link.unlink(missing_ok=True)
        link.symlink_to(os.path.relpath(target, sherpa_libs))

    return importlib.import_module("sherpa_onnx")
