-- Veetee M4.1 control-plane foundation. Apply with psql in a transaction.
BEGIN;

CREATE TABLE IF NOT EXISTS veetee_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_users (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_agents (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    role_prompt TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',
    address_style TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'vi-VN',
    detail_level TEXT NOT NULL DEFAULT 'adaptive',
    response_style TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    voice_id TEXT NOT NULL DEFAULT '',
    intent_strategy TEXT NOT NULL DEFAULT 'function_call',
    memory_enabled BOOLEAN NOT NULL DEFAULT true,
    memory_min_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8
        CHECK (memory_min_confidence BETWEEN 0 AND 1),
    tool_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name)
);

CREATE TABLE IF NOT EXISTS veetee_devices (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES veetee_agents(id) ON DELETE SET NULL,
    device_id TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '',
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, device_id)
);

CREATE TABLE IF NOT EXISTS veetee_memories (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES veetee_agents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('working', 'episodic', 'profile')),
    content TEXT NOT NULL,
    provenance TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_audit_events (
    id UUID PRIMARY KEY,
    actor_user_id UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_agents_owner_idx ON veetee_agents(owner_user_id);
CREATE INDEX IF NOT EXISTS veetee_devices_owner_idx ON veetee_devices(owner_user_id);
CREATE INDEX IF NOT EXISTS veetee_memories_scope_idx ON veetee_memories(owner_user_id, agent_id);
CREATE INDEX IF NOT EXISTS veetee_audit_resource_idx
    ON veetee_audit_events(resource_type, resource_id, created_at DESC);

INSERT INTO veetee_schema_migrations(version)
VALUES ('001_control_plane')
ON CONFLICT (version) DO NOTHING;

COMMIT;
