-- Down migration for 008_m6_corrections_context.sql
-- Fail-closed: refuses to run if data exists.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_correction_sets) THEN
        RAISE EXCEPTION 'veetee_correction_sets contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_agent_context_providers) THEN
        RAISE EXCEPTION 'veetee_agent_context_providers contains data; down migration aborted to prevent data loss';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_agent_context_providers;
DROP TABLE IF EXISTS veetee_correction_rules;
DROP TABLE IF EXISTS veetee_correction_sets;

DELETE FROM veetee_schema_migrations WHERE version = '008_m6_corrections_context';

COMMIT;
