BEGIN;

ALTER TABLE veetee_devices
    ADD COLUMN IF NOT EXISTS transcript_consent BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS consent_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS consent_policy_version INTEGER NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'veetee_devices_transcript_consent_version_check'
          AND conrelid = 'veetee_devices'::regclass
    ) THEN
        ALTER TABLE veetee_devices
            ADD CONSTRAINT veetee_devices_transcript_consent_version_check
            CHECK (
                consent_policy_version >= 1
                AND (NOT transcript_consent OR btrim(consent_version) <> '')
            );
    END IF;
END $$;

INSERT INTO veetee_schema_migrations(version)
VALUES ('011_m6_consent_transcript')
ON CONFLICT (version) DO NOTHING;

COMMIT;
