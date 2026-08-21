BEGIN;

CREATE TABLE IF NOT EXISTS veetee_provider_configs (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES veetee_agents(id) ON DELETE CASCADE,
    provider_kind TEXT NOT NULL CHECK (provider_kind IN ('asr', 'llm', 'tts', 'intent')),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret_reference TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, provider_kind)
);

CREATE TABLE IF NOT EXISTS veetee_conversations (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES veetee_agents(id) ON DELETE SET NULL,
    device_id UUID REFERENCES veetee_devices(id) ON DELETE SET NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    locale TEXT NOT NULL DEFAULT 'vi-VN',
    turn_count INTEGER NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    retention_until TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS veetee_provider_configs_owner_idx
    ON veetee_provider_configs(owner_user_id, agent_id);
CREATE INDEX IF NOT EXISTS veetee_conversations_owner_idx
    ON veetee_conversations(owner_user_id, started_at DESC);

INSERT INTO veetee_schema_migrations(version)
VALUES ('002_runtime_control_plane')
ON CONFLICT (version) DO NOTHING;

COMMIT;
