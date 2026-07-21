from __future__ import annotations

import hashlib
from pathlib import Path

from huggingface_hub import snapshot_download


SERVER_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = SERVER_ROOT / "models" / "vieneu-v3-turbo-native"
REPOSITORY = "lastudio-community/VieNeu-TTS-v3-Turbo-CPP"
REVISION = "a7b4f40050d4ff6d5225d8fd4fe6d571913844a2"

# Native assets are intentionally opt-in: they are useful for a benchmark or a
# dedicated worker, but the streaming ONNX path remains the default voice path.
FILES = {
    "config.json": "a9f8d9c4b4736448ab355d1a98cfe48f5e39aecf2916c37b0806c228612e9a2d",
    "tokenizer.json": "6cc6bcbe380b8c37bd9f2514e37c5dfa3e00e122c6e3125dae5c4afe48e39158",
    "voices_v3_turbo.json": "9f5ed60eb0be6af3447b097d1a5bfb90214d4292e83dd4f2e123e2b27896a02d",
    "backbone.gguf": "d2d86fb181f27b42a1ccb61d84979e90a4aeeb135c2e52d0a738cb544b874634",
    "vieneu_v3_heads.npz": "efed55d6e55bc02971a77f387d9551a3108a1e0056be1f907a11caccb90c57f3",
    "acoustic/vieneu_acoustic_weights.npz": "ca1230b000192b0050bbcd97f810d4cc471550c1102b811046b5af5782166e8b",
    "codec/moss_audio_tokenizer_decode_full.onnx": "0fbbafe3fd4afa2a019af5c5ced204af6e2d1db044fa40f021525d2aee95b4ac",
    "codec/moss_audio_tokenizer_decode_shared.data": "e69d52e0f4e84ca27850557ee54face46632d3a5a16c89bd246c7c408466dcad",
    "codec/moss_audio_tokenizer_encode.onnx": "eadea4a645abdcf98714c7aead122ee2ce7da6e080f9f80b977cd1ca8e19473a",
    "codec/moss_audio_tokenizer_encode.data": "aa751265b2bab2887eac224484546b194875aa7494b607115439b3dc6b228a2c",
    "speaker_encoder.onnx": "a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73",
    "denoiser.onnx": "b7621953291cfe05e695a9c0ff4255aa2f93239fc17c26627e18b7b6b8f72f0b",
}


def main() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPOSITORY,
        revision=REVISION,
        local_dir=MODEL_ROOT,
        allow_patterns=list(FILES),
    )
    for relative, expected in FILES.items():
        path = MODEL_ROOT / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {relative}: {actual}")
    print(f"Verified native VieNeu assets in {MODEL_ROOT}")


if __name__ == "__main__":
    main()
