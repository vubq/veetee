-- Veetee M6.2: agent lifecycle (immutable snapshots, templates, tags) and
-- conversation history with opt-in versioned transcript consent.
BEGIN;

-- Immutable agent configuration snapshots/revisions.
CREATE TABLE IF NOT EXISTS veetee_agent_snapshots (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES veetee_agents(id) ON DELETE CASCADE,
    source_version INTEGER NOT NULL CHECK (source_version > 0),
    schema_version TEXT NOT NULL CHECK (schema_version <> ''),
    checksum TEXT NOT NULL CHECK (char_length(checksum) = 64),
    config JSONB NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('manual', 'pre_restore')),
    created_by UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_agent_snapshots_agent_idx
    ON veetee_agent_snapshots(agent_id, source_version DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS veetee_agent_snapshots_owner_idx
    ON veetee_agent_snapshots(owner_user_id);

-- Snapshots are append-only: any UPDATE is rejected at the database level so a
-- buggy code path can never rewrite history. Deletion stays possible only via
-- the cascades of fully deleting the owning user/agent.
CREATE OR REPLACE FUNCTION veetee_agent_snapshots_forbid_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'veetee_agent_snapshots rows are immutable';
END;
$$;

DROP TRIGGER IF EXISTS veetee_agent_snapshots_no_update ON veetee_agent_snapshots;
CREATE TRIGGER veetee_agent_snapshots_no_update
    BEFORE UPDATE ON veetee_agent_snapshots
    FOR EACH ROW EXECUTE FUNCTION veetee_agent_snapshots_forbid_update();

-- Tenant-scoped agent templates; creating an agent from a template copies the
-- config and never links back, so later template edits stay independent.
CREATE TABLE IF NOT EXISTS veetee_agent_templates (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name <> ''),
    description TEXT NOT NULL DEFAULT '',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name)
);

-- Tenant-safe tags plus agent links; every access path joins through the tag's
-- owner so one tenant can never attach to another tenant's agent.
CREATE TABLE IF NOT EXISTS veetee_agent_tags (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name)
);

CREATE TABLE IF NOT EXISTS veetee_agent_tag_links (
    tag_id UUID NOT NULL REFERENCES veetee_agent_tags(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES veetee_agents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tag_id, agent_id)
);

CREATE INDEX IF NOT EXISTS veetee_agent_tag_links_agent_idx
    ON veetee_agent_tag_links(agent_id);

-- Conversation lifecycle columns on top of the M2 table; existing M1-M5 rows
-- keep working through the non-destructive defaults below.
ALTER TABLE veetee_conversations
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'ended')),
    ADD COLUMN IF NOT EXISTS transcript_consent BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS consent_version TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Per-turn transcript rows. Raw audio is never persisted here or anywhere else;
-- these columns hold text produced by the pipeline for conversations whose
-- owner granted versioned consent. System prompt turns are not stored.
CREATE TABLE IF NOT EXISTS veetee_conversation_turns (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES veetee_conversations(id) ON DELETE CASCADE,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    turn_id TEXT NOT NULL CHECK (turn_id <> ''),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    raw_transcript TEXT NOT NULL DEFAULT '',
    normalized_text TEXT NOT NULL DEFAULT '',
    model_text TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_call_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, ordinal),
    UNIQUE (conversation_id, turn_id)
);

CREATE INDEX IF NOT EXISTS veetee_conversation_turns_owner_idx
    ON veetee_conversation_turns(owner_user_id);
CREATE INDEX IF NOT EXISTS veetee_conversation_turns_conversation_idx
    ON veetee_conversation_turns(conversation_id, ordinal);

INSERT INTO veetee_schema_migrations(version)
VALUES ('006_m6_agent_lifecycle_history')
ON CONFLICT (version) DO NOTHING;

COMMIT;
