-- Veetee M6.8: System Administration & Quota Governance
-- User management, password reset tokens, typed system settings,
-- audit search support, and per-user/global quota governance.

BEGIN;

CREATE TABLE IF NOT EXISTS veetee_password_reset_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_by UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_password_reset_tokens_user_idx
    ON veetee_password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS veetee_password_reset_tokens_hash_idx
    ON veetee_password_reset_tokens(token_hash);

CREATE TABLE IF NOT EXISTS veetee_system_settings (
    key TEXT PRIMARY KEY CHECK (key <> ''),
    value_json JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO veetee_system_settings (key, value_json, version)
VALUES
    ('conversation_retention_days', '30'::jsonb, 1),
    ('quota_enabled', 'false'::jsonb, 1),
    ('default_quota_llm_tokens_per_day', 'null'::jsonb, 1),
    ('default_quota_tts_chars_per_day', 'null'::jsonb, 1),
    ('default_quota_tool_calls_per_minute', 'null'::jsonb, 1),
    ('default_quota_rag_bytes_per_month', 'null'::jsonb, 1)
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS veetee_user_quotas (
    user_id UUID PRIMARY KEY REFERENCES veetee_users(id) ON DELETE CASCADE,
    llm_tokens_per_day BIGINT CHECK (llm_tokens_per_day IS NULL OR llm_tokens_per_day >= 0),
    tts_chars_per_day BIGINT CHECK (tts_chars_per_day IS NULL OR tts_chars_per_day >= 0),
    tool_calls_per_minute INTEGER CHECK (tool_calls_per_minute IS NULL OR tool_calls_per_minute >= 0),
    rag_bytes_per_month BIGINT CHECK (rag_bytes_per_month IS NULL OR rag_bytes_per_month >= 0),
    enabled BOOLEAN NOT NULL DEFAULT false,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_quota_usage_buckets (
    user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    metric_type TEXT NOT NULL CHECK (metric_type IN ('llm_tokens_day', 'tts_chars_day', 'tool_calls_minute', 'rag_bytes_month')),
    window_start TIMESTAMPTZ NOT NULL,
    used_amount BIGINT NOT NULL DEFAULT 0 CHECK (used_amount >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, metric_type, window_start)
);

CREATE INDEX IF NOT EXISTS veetee_quota_usage_buckets_lookup_idx
    ON veetee_quota_usage_buckets(user_id, metric_type, window_start);

INSERT INTO veetee_schema_migrations(version)
VALUES ('010_m6_administration')
ON CONFLICT (version) DO NOTHING;

COMMIT;
