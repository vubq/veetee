-- Down migration for 0010_m6_administration.sql
-- Fail-closed: refuses to run if custom data exists.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM veetee_quota_usage_buckets) THEN
        RAISE EXCEPTION 'veetee_quota_usage_buckets contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_user_quotas) THEN
        RAISE EXCEPTION 'veetee_user_quotas contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (SELECT 1 FROM veetee_password_reset_tokens) THEN
        RAISE EXCEPTION 'veetee_password_reset_tokens contains data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (
        SELECT 1 FROM veetee_system_settings
        WHERE key NOT IN (
            'conversation_retention_days',
            'quota_enabled',
            'default_quota_llm_tokens_per_day',
            'default_quota_tts_chars_per_day',
            'default_quota_tool_calls_per_minute',
            'default_quota_rag_bytes_per_month'
        )
    ) THEN
        RAISE EXCEPTION 'veetee_system_settings contains custom data; down migration aborted to prevent data loss';
    END IF;
    IF EXISTS (
        SELECT 1 FROM veetee_system_settings
        WHERE version <> 1 OR updated_by IS NOT NULL OR
            (key = 'conversation_retention_days' AND value_json <> '30'::jsonb) OR
            (key = 'quota_enabled' AND value_json <> 'false'::jsonb) OR
            (key LIKE 'default_quota_%' AND value_json <> 'null'::jsonb)
    ) THEN
        RAISE EXCEPTION 'veetee_system_settings contains modified data; down migration aborted to prevent data loss';
    END IF;
END $$;

DROP TABLE IF EXISTS veetee_quota_usage_buckets;
DROP TABLE IF EXISTS veetee_user_quotas;
DROP TABLE IF EXISTS veetee_system_settings;
DROP TABLE IF EXISTS veetee_password_reset_tokens;

DELETE FROM veetee_schema_migrations WHERE version = '010_m6_administration';

COMMIT;
