BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM veetee_devices
        WHERE transcript_consent
           OR consent_version <> ''
           OR consent_policy_version <> 1
    ) THEN
        RAISE EXCEPTION
            'veetee_devices contains transcript consent decisions; down migration aborted to prevent data loss';
    END IF;
END $$;

ALTER TABLE veetee_devices
    DROP CONSTRAINT IF EXISTS veetee_devices_transcript_consent_version_check;

ALTER TABLE veetee_devices
    DROP COLUMN IF EXISTS transcript_consent,
    DROP COLUMN IF EXISTS consent_version,
    DROP COLUMN IF EXISTS consent_policy_version;

DELETE FROM veetee_schema_migrations
WHERE version = '011_m6_consent_transcript';

COMMIT;
