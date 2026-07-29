from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVER_ROOT))

from scripts import configure_voice_env  # noqa: E402


def test_voice_env_sync_uses_cliproxy_without_a_9router_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    voice_example = tmp_path / "voice.env.example"
    voice_env = tmp_path / "voice.env"
    manager_env = tmp_path / "manager.env"
    cliproxy_config = tmp_path / "cliproxy.yaml"
    voice_example.write_text(
        "\n".join(
            (
                "VEETEE_HOST=127.0.0.1",
                "VEETEE_MANAGER_INTERNAL_TOKEN=placeholder",
                "VEETEE_LAB_ALLOWED_ORIGINS=http://127.0.0.1:8081",
                "VEETEE_CLIPROXY_BASE_URL=http://127.0.0.1:8317/v1",
                "VEETEE_CLIPROXY_API_KEY=placeholder",
                "VEETEE_CLIPROXY_MODEL=gpt-5.6-terra",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    manager_env.write_text(
        "VEETEE_INTERNAL_SERVICE_TOKEN=manager-token-long-enough-for-validation\n",
        encoding="utf-8",
    )
    cliproxy_config.write_text(
        'api-keys:\n  - "cliproxy-test-key"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(configure_voice_env, "VOICE_EXAMPLE", voice_example)
    monkeypatch.setattr(configure_voice_env, "VOICE_ENV", voice_env)
    monkeypatch.setattr(configure_voice_env, "MANAGER_ENV", manager_env)
    monkeypatch.setattr(configure_voice_env, "CLIPROXY_CONFIG", cliproxy_config)

    configure_voice_env.main()

    rendered = configure_voice_env.parse_environment(voice_env)
    # The sync script must append the cap even if an older local template lacks it.
    assert rendered["OPENBLAS_NUM_THREADS"] == "1"
    assert rendered["VEETEE_CLIPROXY_API_KEY"] == "cliproxy-test-key"
    assert rendered["VEETEE_CLIPROXY_BASE_URL"] == "http://127.0.0.1:8317/v1"
    assert rendered["VEETEE_CLIPROXY_MODEL"] == "gpt-5.6-terra"
    assert "VEETEE_9ROUTER_API_KEY" not in rendered
    assert voice_env.stat().st_mode & 0o777 == 0o600
    assert "cliproxy-test-key" not in capsys.readouterr().out


def test_voice_example_pins_openblas_before_python_start() -> None:
    rendered = configure_voice_env.parse_environment(
        SERVER_ROOT / "apps/voice-server/.env.example"
    )

    assert rendered["OPENBLAS_NUM_THREADS"] == "1"


def test_bare_voice_commands_pin_openblas_before_python_start() -> None:
    package = json.loads((SERVER_ROOT / "package.json").read_text(encoding="utf-8"))

    for command in ("dev:voice", "test:voice:local-e2e", "models:benchmark"):
        assert package["scripts"][command].startswith("OPENBLAS_NUM_THREADS=1 ")


def test_voice_env_sync_rejects_missing_cliproxy_keys(tmp_path: Path) -> None:
    config = tmp_path / "cliproxy.yaml"
    config.write_text("api-keys: []\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="api-keys"):
        configure_voice_env.first_yaml_list_item(config, "api-keys")
