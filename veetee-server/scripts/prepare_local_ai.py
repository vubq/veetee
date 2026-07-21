from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from huggingface_hub import snapshot_download

SERVER_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = SERVER_ROOT / "models"
MANIFEST_PATH = SERVER_ROOT / "apps/voice-server/model-manifest.json"


def download_models(manifest: dict[str, Any]) -> None:
    models = manifest["models"]
    for model_id, model in models.items():
        destination = MODEL_ROOT / model["directory"]
        if model_id == "silero_vad":
            destination.mkdir(parents=True, exist_ok=True)
            url = (
                "https://raw.githubusercontent.com/snakers4/silero-vad/"
                f"{model['revision']}/src/silero_vad/data/silero_vad.onnx"
            )
            target = destination / "silero_vad.onnx"
            if not target.is_file():
                urllib.request.urlretrieve(url, target)  # noqa: S310
            continue
        allow_patterns = list(model["files"])
        snapshot_download(
            repo_id=model["repository"],
            revision=model["revision"],
            local_dir=destination,
            allow_patterns=allow_patterns,
        )


def verify_models(manifest: dict[str, Any]) -> None:
    for model_id, model in manifest["models"].items():
        directory = MODEL_ROOT / model["directory"]
        for relative_path, expected in model["files"].items():
            path = directory / relative_path
            if not path.is_file():
                raise FileNotFoundError(f"{model_id}: missing {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected:
                raise ValueError(f"{model_id}: checksum mismatch for {path}")


def link_sherpa_runtime() -> None:
    onnx_spec = importlib.util.find_spec("onnxruntime")
    sherpa_spec = importlib.util.find_spec("sherpa_onnx")
    if not onnx_spec or not onnx_spec.origin or not sherpa_spec or not sherpa_spec.origin:
        raise RuntimeError("Install onnxruntime and sherpa-onnx before preparing models")
    runtime_files = sorted((Path(onnx_spec.origin).parent / "capi").glob("libonnxruntime.so.*"))
    if not runtime_files:
        raise RuntimeError("onnxruntime shared library is missing")
    site_packages = Path(sherpa_spec.origin).parents[1]
    link_dir = site_packages / "sherpa_onnx.libs"
    link_dir.mkdir(exist_ok=True)
    link = link_dir / "libonnxruntime.so"
    link.unlink(missing_ok=True)
    link.symlink_to(os.path.relpath(runtime_files[-1], link_dir))


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    MODEL_ROOT.mkdir(exist_ok=True)
    download_models(manifest)
    verify_models(manifest)
    link_sherpa_runtime()
    print(f"Local AI models verified in {MODEL_ROOT}")


if __name__ == "__main__":
    main()
