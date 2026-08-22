-- Rollback 004: the table only stores rate-limit counters, no business data,
-- so dropping it is always safe and must not fail closed like migration 003.
BEGIN;

DROP TABLE IF EXISTS veetee_login_attempts;

DELETE FROM veetee_schema_migrations WHERE version = '004_login_rate_limit';

COMMIT;
