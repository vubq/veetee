BEGIN;
DROP TABLE IF EXISTS veetee_conversations;
DROP TABLE IF EXISTS veetee_provider_configs;
DELETE FROM veetee_schema_migrations WHERE version = '002_runtime_control_plane';
COMMIT;
