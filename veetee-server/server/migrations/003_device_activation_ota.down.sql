BEGIN;

-- Fail closed when M5 data exists: dropping it would make OTA artifacts or
-- activation/binding history unrecoverable.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_device_activations)
       OR EXISTS (SELECT 1 FROM veetee_device_bind_attempts)
       OR EXISTS (SELECT 1 FROM veetee_device_bind_receipts)
       OR EXISTS (SELECT 1 FROM veetee_firmware_artifacts)
       OR EXISTS (SELECT 1 FROM veetee_firmware_releases)
    THEN
        RAISE EXCEPTION 'M5 rollback refused: device activation or OTA data exists';
    END IF;
END $$;

DROP TABLE veetee_firmware_releases;
DROP TABLE veetee_firmware_artifacts;
DROP TABLE veetee_device_bind_receipts;
DROP TABLE veetee_device_bind_attempts;
DROP TABLE veetee_device_activations;
ALTER TABLE veetee_devices DROP CONSTRAINT veetee_devices_device_id_key;
ALTER TABLE veetee_devices DROP COLUMN board;
ALTER TABLE veetee_devices DROP COLUMN chip;
ALTER TABLE veetee_devices DROP COLUMN partition_name;
ALTER TABLE veetee_devices DROP COLUMN firmware_version;
ALTER TABLE veetee_devices DROP COLUMN client_id;
ALTER TABLE veetee_devices DROP COLUMN updated_at;
ALTER TABLE veetee_devices
    ADD CONSTRAINT veetee_devices_owner_user_id_device_id_key
    UNIQUE (owner_user_id, device_id);
DELETE FROM veetee_schema_migrations WHERE version = '003_device_activation_ota';

COMMIT;
