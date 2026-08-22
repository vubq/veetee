-- Down migration for 007_m6_knowledge_rag.sql
-- Fail-closed: refuses to run if data exists to prevent accidental loss.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_datasets) THEN
        RAISE EXCEPTION 'veetee_datasets contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_documents) THEN
        RAISE EXCEPTION 'veetee_documents contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_chunks) THEN
        RAISE EXCEPTION 'veetee_chunks contains data; down migration aborted to prevent data loss';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_agent_datasets;
DROP TABLE IF EXISTS veetee_chunks;
DROP TABLE IF EXISTS veetee_documents;
DROP TABLE IF EXISTS veetee_datasets;

DELETE FROM veetee_schema_migrations WHERE version = '007_m6_knowledge_rag';

COMMIT;
