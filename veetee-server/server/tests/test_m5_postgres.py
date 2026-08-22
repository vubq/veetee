"""Migration 003 safety assertions."""

from pathlib import Path


def test_migration_003_is_fail_closed_and_preserves_m4_data() -> None:
    migrations = Path(__file__).parents[1] / "migrations"
    up = (migrations / "003_device_activation_ota.sql").read_text(encoding="utf-8")
    down = (migrations / "003_device_activation_ota.down.sql").read_text(encoding="utf-8")
    assert "DELETE FROM veetee_devices" not in up
    assert "duplicate global device_id values" in up
    assert "UNIQUE (device_id)" in up
    assert "veetee_device_bind_receipts" in up
    assert "ADD COLUMN IF NOT EXISTS activation_code_hash" not in up
    assert "failed_attempts INTEGER" not in up
    assert "RAISE EXCEPTION" in down
    assert "DROP TABLE IF EXISTS" not in down
