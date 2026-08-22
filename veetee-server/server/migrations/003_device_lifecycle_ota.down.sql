-- Down migration for Veetee M5 device lifecycle and OTA. Apply with psql in a transaction.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_device_activation_challenges)
       OR EXISTS (SELECT 1 FROM veetee_device_credentials)
       OR EXISTS (SELECT 1 FROM veetee_device_enrollments)
       OR EXISTS (SELECT 1 FROM veetee_device_binding_history)
       OR EXISTS (SELECT 1 FROM veetee_idempotency_operations)
       OR EXISTS (SELECT 1 FROM veetee_ota_artifacts)
       OR EXISTS (SELECT 1 FROM veetee_ota_releases)
       OR EXISTS (SELECT 1 FROM veetee_ota_rollouts)
       OR EXISTS (SELECT 1 FROM veetee_ota_offers)
       OR EXISTS (SELECT 1 FROM veetee_ota_rollback_authorizations)
       OR EXISTS (SELECT 1 FROM veetee_ota_reports)
       OR EXISTS (
           SELECT 1 FROM veetee_devices
           WHERE owner_user_id IS NULL OR client_id <> '' OR status <> 'recovery_required'
              OR board <> '' OR chip <> '' OR partition <> ''
              OR current_firmware_version <> '' OR observed_firmware_version <> ''
              OR auto_update IS NOT TRUE OR channel <> 'stable' OR cohort <> ''
              OR version <> 1 OR last_discovery_at IS NOT NULL
       ) THEN
        RAISE EXCEPTION 'Cannot roll back M5 while M5 lifecycle or OTA data exists';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_ota_reports;
DROP TABLE IF EXISTS veetee_ota_rollback_authorizations;
DROP TABLE IF EXISTS veetee_ota_offers;
DROP TABLE IF EXISTS veetee_ota_rollouts;
DROP TABLE IF EXISTS veetee_ota_releases;
DROP TABLE IF EXISTS veetee_ota_artifacts;
DROP TABLE IF EXISTS veetee_device_binding_history;
DROP TABLE IF EXISTS veetee_idempotency_operations;
DROP TABLE IF EXISTS veetee_device_credentials;
DROP TABLE IF EXISTS veetee_device_enrollments;
DROP TABLE IF EXISTS veetee_device_activation_challenges;
DROP FUNCTION IF EXISTS veetee_reject_immutable_change();
DROP FUNCTION IF EXISTS veetee_restrict_release_change();

-- Remove added columns from veetee_devices
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS updated_at;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS last_discovery_at;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS version;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS cohort;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS channel;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS auto_update;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS current_firmware_version;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS observed_firmware_version;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS partition;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS chip;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS board;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS status;
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS client_id;

-- Drop device_id unique constraint
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'veetee_devices_device_id_key'
    ) THEN
        ALTER TABLE veetee_devices DROP CONSTRAINT veetee_devices_device_id_key;
    END IF;
END $$;

-- Restore owner_user_id NOT NULL and composite uniqueness
ALTER TABLE veetee_devices ALTER COLUMN owner_user_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'veetee_devices_owner_user_id_device_id_key'
    ) THEN
        ALTER TABLE veetee_devices ADD CONSTRAINT veetee_devices_owner_user_id_device_id_key UNIQUE (owner_user_id, device_id);
    END IF;
END $$;

DELETE FROM veetee_schema_migrations WHERE version = '003_device_lifecycle_ota';

COMMIT;
