-- Veetee M4/M5 audit hardening: persistent login throttling state.
-- Only the SHA-256 hash of the normalized login identifier is stored, never
-- the raw address, so the table carries no PII and holds only quota counters.
BEGIN;

CREATE TABLE IF NOT EXISTS veetee_login_attempts (
    id UUID PRIMARY KEY,
    email_hash TEXT NOT NULL CHECK (email_hash ~ '^[0-9a-f]{64}$'),
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_login_attempts_scope_idx
    ON veetee_login_attempts(email_hash, attempted_at DESC);

INSERT INTO veetee_schema_migrations(version)
VALUES ('004_login_rate_limit')
ON CONFLICT (version) DO NOTHING;

COMMIT;
