-- Veetee M6.6: tenant-scoped external integration endpoints and per-agent
-- integration permissions. Endpoint URLs are HTTPS-only at the storage layer;
-- runtime calls additionally enforce the environment host allowlist, SSRF
-- checks and rate limits. No secret value is ever stored: auth_header_env only
-- names the environment variable holding a bearer token.

BEGIN;

CREATE TABLE IF NOT EXISTS veetee_external_endpoints (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name <> ''),
    url TEXT NOT NULL CHECK (url LIKE 'https://%'),
    auth_header_env TEXT CHECK (auth_header_env IS NULL OR auth_header_env <> ''),
    enabled BOOLEAN NOT NULL DEFAULT true,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name),
    UNIQUE (id, owner_user_id)
);

CREATE INDEX IF NOT EXISTS veetee_external_endpoints_owner_idx
    ON veetee_external_endpoints(owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_agent_integration_permissions (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL,
    endpoint_id UUID NOT NULL,
    can_list BOOLEAN NOT NULL DEFAULT false,
    can_call BOOLEAN NOT NULL DEFAULT false,
    rate_limit_calls INTEGER NOT NULL DEFAULT 30 CHECK (rate_limit_calls > 0),
    rate_limit_window_seconds INTEGER NOT NULL DEFAULT 60 CHECK (rate_limit_window_seconds > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_id, endpoint_id),
    FOREIGN KEY (endpoint_id, owner_user_id)
        REFERENCES veetee_external_endpoints(id, owner_user_id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id, owner_user_id)
        REFERENCES veetee_agents(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_agent_integration_perm_agent_idx
    ON veetee_agent_integration_permissions(agent_id);
CREATE INDEX IF NOT EXISTS veetee_agent_integration_perm_owner_idx
    ON veetee_agent_integration_permissions(owner_user_id);

INSERT INTO veetee_schema_migrations(version)
VALUES ('009_m6_tool_integrations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
