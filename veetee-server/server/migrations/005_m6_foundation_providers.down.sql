-- Rollback 005: Fail closed if M6 user or provider data has been modified.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM veetee_users
        WHERE status <> 'active' OR version <> 1 OR last_login_at IS NOT NULL
    )
       OR EXISTS (SELECT 1 FROM veetee_providers WHERE version > 1 OR NOT enabled OR NOT is_default)
    THEN
        RAISE EXCEPTION 'M6 rollback refused: modified user status or provider state exists';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_providers;

ALTER TABLE veetee_users
    DROP COLUMN IF EXISTS last_login_at,
    DROP COLUMN IF EXISTS version,
    DROP COLUMN IF EXISTS status;

DELETE FROM veetee_schema_migrations WHERE version = '005_m6_foundation_providers';

COMMIT;
