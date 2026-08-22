-- Down migration for 009_m6_tool_integrations.sql
-- Fail-closed: refuses to run if data exists.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_agent_integration_permissions) THEN
        RAISE EXCEPTION 'veetee_agent_integration_permissions contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_external_endpoints) THEN
        RAISE EXCEPTION 'veetee_external_endpoints contains data; down migration aborted to prevent data loss';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_agent_integration_permissions;
DROP TABLE IF EXISTS veetee_external_endpoints;

DELETE FROM veetee_schema_migrations WHERE version = '009_m6_tool_integrations';

COMMIT;
