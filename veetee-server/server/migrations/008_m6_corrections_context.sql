-- Veetee M6.5: Correction rules and Context Provider Registry persistence tables.

BEGIN;

CREATE TABLE IF NOT EXISTS veetee_correction_sets (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID,
    name TEXT NOT NULL CHECK (name <> ''),
    enabled BOOLEAN NOT NULL DEFAULT true,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name),
    UNIQUE (id, owner_user_id),
    FOREIGN KEY (agent_id, owner_user_id)
        REFERENCES veetee_agents(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_correction_sets_owner_idx
    ON veetee_correction_sets(owner_user_id);
CREATE INDEX IF NOT EXISTS veetee_correction_sets_agent_idx
    ON veetee_correction_sets(agent_id);

CREATE TABLE IF NOT EXISTS veetee_correction_rules (
    id UUID PRIMARY KEY,
    set_id UUID NOT NULL,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    rule_type TEXT NOT NULL CHECK (rule_type IN ('exact', 'phrase')),
    pattern TEXT NOT NULL CHECK (pattern <> ''),
    replacement TEXT NOT NULL,
    case_sensitive BOOLEAN NOT NULL DEFAULT false,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (set_id, owner_user_id)
        REFERENCES veetee_correction_sets(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_correction_rules_set_idx
    ON veetee_correction_rules(set_id, ordinal);
CREATE INDEX IF NOT EXISTS veetee_correction_rules_owner_idx
    ON veetee_correction_rules(owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_agent_context_providers (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL,
    provider_type TEXT NOT NULL CHECK (provider_type IN ('runtime', 'memory', 'knowledge_fts', 'weather')),
    enabled BOOLEAN NOT NULL DEFAULT true,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    timeout_ms INTEGER NOT NULL DEFAULT 2000 CHECK (timeout_ms > 0),
    cache_ttl_seconds INTEGER NOT NULL DEFAULT 0 CHECK (cache_ttl_seconds >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, provider_type),
    FOREIGN KEY (agent_id, owner_user_id)
        REFERENCES veetee_agents(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_agent_ctx_prov_agent_idx
    ON veetee_agent_context_providers(agent_id, ordinal);

INSERT INTO veetee_schema_migrations(version)
VALUES ('008_m6_corrections_context')
ON CONFLICT (version) DO NOTHING;

COMMIT;
