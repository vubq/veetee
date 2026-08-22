-- Veetee M6.4: Knowledge / RAG persistence tables.
-- PostgreSQL native FTS with 'simple' regconfig, simple text/markdown document ingest,
-- bounded chunks, tenant isolation, and agent-dataset assignments.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS veetee_agents_id_owner_unique_idx
    ON veetee_agents(id, owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_datasets (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (name <> ''),
    description TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, name),
    UNIQUE (id, owner_user_id)
);

CREATE INDEX IF NOT EXISTS veetee_datasets_owner_idx
    ON veetee_datasets(owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_documents (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL,
    filename TEXT NOT NULL CHECK (filename <> ''),
    media_type TEXT NOT NULL CHECK (media_type IN ('text/plain', 'text/markdown')),
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    sha256 TEXT NOT NULL CHECK (char_length(sha256) = 64),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    error_code TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, sha256),
    UNIQUE (id, owner_user_id, dataset_id),
    FOREIGN KEY (dataset_id, owner_user_id)
        REFERENCES veetee_datasets(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_documents_dataset_idx
    ON veetee_documents(dataset_id);
CREATE INDEX IF NOT EXISTS veetee_documents_owner_idx
    ON veetee_documents(owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_chunks (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL,
    document_id UUID NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    content TEXT NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end >= char_start),
    token_estimate INTEGER NOT NULL CHECK (token_estimate >= 0),
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (document_id, owner_user_id, dataset_id)
        REFERENCES veetee_documents(id, owner_user_id, dataset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_chunks_content_tsv_idx
    ON veetee_chunks USING gin (content_tsv);
CREATE INDEX IF NOT EXISTS veetee_chunks_doc_idx
    ON veetee_chunks(document_id, ordinal);
CREATE INDEX IF NOT EXISTS veetee_chunks_dataset_idx
    ON veetee_chunks(dataset_id);
CREATE INDEX IF NOT EXISTS veetee_chunks_owner_idx
    ON veetee_chunks(owner_user_id);

CREATE TABLE IF NOT EXISTS veetee_agent_datasets (
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL,
    dataset_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, dataset_id),
    FOREIGN KEY (agent_id, owner_user_id)
        REFERENCES veetee_agents(id, owner_user_id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, owner_user_id)
        REFERENCES veetee_datasets(id, owner_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS veetee_agent_datasets_dataset_idx
    ON veetee_agent_datasets(dataset_id);

INSERT INTO veetee_schema_migrations(version)
VALUES ('007_m6_knowledge_rag')
ON CONFLICT (version) DO NOTHING;

COMMIT;
