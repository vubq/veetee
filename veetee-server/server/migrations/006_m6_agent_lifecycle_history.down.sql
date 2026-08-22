-- Rollback 006. Fail closed: transcript turns, snapshots, templates, tags and
-- consent state are M6 data that cannot be regenerated once the schema support
-- is removed, so the downgrade refuses to run while any of it exists.
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_conversation_turns)
       OR EXISTS (SELECT 1 FROM veetee_agent_snapshots)
       OR EXISTS (SELECT 1 FROM veetee_agent_templates)
       OR EXISTS (SELECT 1 FROM veetee_agent_tags)
       OR EXISTS (
           SELECT 1 FROM veetee_conversations
           WHERE transcript_consent
              OR consent_version <> ''
              OR deleted_at IS NOT NULL
       )
    THEN
        RAISE EXCEPTION
            'M6.2 rollback refused: conversation history or agent lifecycle data exists';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_conversation_turns;
DROP TABLE IF EXISTS veetee_agent_tag_links;
DROP TABLE IF EXISTS veetee_agent_tags;
DROP TABLE IF EXISTS veetee_agent_templates;
DROP TRIGGER IF EXISTS veetee_agent_snapshots_no_update ON veetee_agent_snapshots;
DROP FUNCTION IF EXISTS veetee_agent_snapshots_forbid_update();
DROP TABLE IF EXISTS veetee_agent_snapshots;

ALTER TABLE veetee_conversations
    DROP COLUMN IF EXISTS updated_at,
    DROP COLUMN IF EXISTS deleted_at,
    DROP COLUMN IF EXISTS consent_version,
    DROP COLUMN IF EXISTS transcript_consent,
    DROP COLUMN IF EXISTS status;

DELETE FROM veetee_schema_migrations WHERE version = '006_m6_agent_lifecycle_history';

COMMIT;
