-- Veetee M6.1 backend foundation: user status, versioning, and global provider state.
BEGIN;

ALTER TABLE veetee_users
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS veetee_providers (
    id UUID PRIMARY KEY,
    provider_kind TEXT NOT NULL CHECK (provider_kind IN ('asr', 'llm', 'tts')),
    provider_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    is_default BOOLEAN NOT NULL DEFAULT false,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    updated_by UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT veetee_providers_default_must_be_enabled CHECK (NOT is_default OR enabled),
    UNIQUE (provider_kind, provider_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS veetee_providers_unique_default_per_kind
    ON veetee_providers (provider_kind) WHERE is_default = true;

-- Seed code-level initial provider entries if not present
INSERT INTO veetee_providers (id, provider_kind, provider_id, enabled, is_default, version)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'asr', 'pho_whisper', true, true, 1),
    ('00000000-0000-0000-0000-000000000002', 'llm', 'omniroute', true, true, 1),
    ('00000000-0000-0000-0000-000000000003', 'tts', 'vieneu', true, true, 1)
ON CONFLICT (provider_kind, provider_id) DO NOTHING;

INSERT INTO veetee_schema_migrations(version)
VALUES ('005_m6_foundation_providers')
ON CONFLICT (version) DO NOTHING;

COMMIT;
