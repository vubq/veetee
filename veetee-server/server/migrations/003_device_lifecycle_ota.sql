-- Veetee M5 device lifecycle and OTA migration. Apply with psql in a transaction.
BEGIN;

-- Upgrade veetee_devices table for unbound/bound lifecycle, metadata and fleet uniqueness
ALTER TABLE veetee_devices ALTER COLUMN owner_user_id DROP NOT NULL;

-- Fail before changing the M4 uniqueness contract. The whole migration remains atomic.
DO $$
DECLARE
    duplicate_count BIGINT;
BEGIN
    SELECT count(*) INTO duplicate_count
    FROM (
        SELECT device_id FROM veetee_devices GROUP BY device_id HAVING count(*) > 1
    ) duplicates;
    IF duplicate_count > 0 THEN
        RAISE EXCEPTION
            'Cannot migrate M5: % duplicate device_id value(s) must be resolved first',
            duplicate_count;
    END IF;
END $$;

-- Remove old composite uniqueness on (owner_user_id, device_id) if present
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'veetee_devices_owner_user_id_device_id_key'
    ) THEN
        ALTER TABLE veetee_devices DROP CONSTRAINT veetee_devices_owner_user_id_device_id_key;
    END IF;
END $$;

-- Enforce fleet-wide uniqueness on device_id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'veetee_devices_device_id_key'
    ) THEN
        ALTER TABLE veetee_devices ADD CONSTRAINT veetee_devices_device_id_key UNIQUE (device_id);
    END IF;
END $$;

-- Add device status, board, chip, partition, version, auto-update, channel and cohort columns
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS client_id TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'unbound';
ALTER TABLE veetee_devices DROP CONSTRAINT IF EXISTS veetee_devices_status_check;
ALTER TABLE veetee_devices ADD CONSTRAINT veetee_devices_status_check
    CHECK (status IN ('unbound', 'binding', 'bound', 'recovery_required', 'revoked'));
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS board TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS chip TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS partition TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS current_firmware_version TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS observed_firmware_version TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS auto_update BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'stable';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS cohort TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0);
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS last_discovery_at TIMESTAMPTZ;

-- M4 did not persist Client-Id or a device credential. Keep ownership and require the
-- authenticated owner/admin recovery endpoint to bind the first production Client-Id.
UPDATE veetee_devices
SET status = 'recovery_required'
WHERE owner_user_id IS NOT NULL AND client_id = '' AND status = 'unbound';

-- Device activation challenges for short-lived 6-digit codes
CREATE TABLE IF NOT EXISTS veetee_device_activation_challenges (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    salt TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_activation_device_idx
    ON veetee_device_activation_challenges(device_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS veetee_activation_one_live_idx
    ON veetee_device_activation_challenges(device_id)
    WHERE consumed_at IS NULL;

-- Out-of-band device enrollment stores only the public verification key.
CREATE TABLE IF NOT EXISTS veetee_device_enrollments (
    device_id TEXT PRIMARY KEY REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    client_id TEXT,
    ed25519_public_key TEXT NOT NULL CHECK (ed25519_public_key ~ '^[0-9a-f]{64}$'),
    provisioned_by UUID NOT NULL REFERENCES veetee_users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ
);
ALTER TABLE veetee_device_activation_challenges ADD COLUMN IF NOT EXISTS proof_verified_at TIMESTAMPTZ;

-- Per-device WebSocket credentials
CREATE TABLE IF NOT EXISTS veetee_device_credentials (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    jti UUID NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('bootstrap', 'recovery', 'ws')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE veetee_device_credentials
    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'ws';
ALTER TABLE veetee_device_credentials DROP CONSTRAINT IF EXISTS veetee_device_credentials_kind_check;
ALTER TABLE veetee_device_credentials ADD CONSTRAINT veetee_device_credentials_kind_check
    CHECK (kind IN ('bootstrap', 'recovery', 'ws'));

CREATE INDEX IF NOT EXISTS veetee_credentials_lookup_idx
    ON veetee_device_credentials(device_id, client_id, kind, status);

-- Device binding audit history
CREATE TABLE IF NOT EXISTS veetee_device_binding_history (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    user_id UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL CHECK (action IN ('activated', 'bound', 'unbound', 'rebound', 'revoked')),
    actor_user_id UUID REFERENCES veetee_users(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_binding_history_device_idx
    ON veetee_device_binding_history(device_id, created_at DESC);

-- Persistent idempotency for security-sensitive lifecycle mutations.
CREATE TABLE IF NOT EXISTS veetee_idempotency_operations (
    id UUID PRIMARY KEY,
    operation_key TEXT NOT NULL,
    actor_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response JSONB NOT NULL,
    resource_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (operation_key, actor_user_id, action)
);

-- Immutable OTA artifacts
CREATE TABLE IF NOT EXISTS veetee_ota_artifacts (
    id UUID PRIMARY KEY,
    board TEXT NOT NULL,
    chip TEXT NOT NULL,
    partition TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size > 0),
    sha256 TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    signature_algorithm TEXT NOT NULL DEFAULT 'ed25519',
    signature_key_id TEXT NOT NULL DEFAULT 'primary',
    provenance TEXT NOT NULL CHECK (char_length(provenance) BETWEEN 1 AND 512),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE veetee_ota_artifacts
    ADD COLUMN IF NOT EXISTS signature_algorithm TEXT NOT NULL DEFAULT 'ed25519';
ALTER TABLE veetee_ota_artifacts
    ADD COLUMN IF NOT EXISTS signature_key_id TEXT NOT NULL DEFAULT 'primary';
ALTER TABLE veetee_ota_artifacts
    ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'legacy-m5';

CREATE INDEX IF NOT EXISTS veetee_artifacts_target_idx
    ON veetee_ota_artifacts(board, chip, partition, sha256);
CREATE UNIQUE INDEX IF NOT EXISTS veetee_artifacts_digest_idx
    ON veetee_ota_artifacts(sha256);

-- Immutable OTA releases
CREATE TABLE IF NOT EXISTS veetee_ota_releases (
    id UUID PRIMARY KEY,
    version TEXT NOT NULL,
    artifact_id UUID NOT NULL REFERENCES veetee_ota_artifacts(id) ON DELETE RESTRICT,
    board TEXT NOT NULL,
    chip TEXT NOT NULL,
    partition TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'stable',
    min_current_version TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL CHECK (char_length(provenance) BETWEEN 1 AND 512),
    rollback_target_id UUID REFERENCES veetee_ota_releases(id) ON DELETE RESTRICT,
    is_published BOOLEAN NOT NULL DEFAULT false,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (board, chip, partition, channel, version)
);
ALTER TABLE veetee_ota_releases
    ADD COLUMN IF NOT EXISTS provenance TEXT NOT NULL DEFAULT 'legacy-m5';
ALTER TABLE veetee_ota_releases
    ADD COLUMN IF NOT EXISTS rollback_target_id UUID
        REFERENCES veetee_ota_releases(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS veetee_releases_target_idx
    ON veetee_ota_releases(board, chip, partition, channel, is_published);

-- Mutable OTA rollouts
CREATE TABLE IF NOT EXISTS veetee_ota_rollouts (
    id UUID PRIMARY KEY,
    release_id UUID NOT NULL REFERENCES veetee_ota_releases(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'stable',
    cohort_percentage INTEGER NOT NULL DEFAULT 100 CHECK (cohort_percentage BETWEEN 0 AND 100),
    status TEXT NOT NULL DEFAULT 'paused' CHECK (status IN ('paused', 'active', 'completed', 'killed')),
    kind TEXT NOT NULL DEFAULT 'release' CHECK (kind IN ('release', 'rollback')),
    rollback_scope TEXT CHECK (rollback_scope IN ('rollout', 'cohort', 'device')),
    rollback_device_id TEXT REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    rollback_cohort TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE veetee_ota_rollouts ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'release';
ALTER TABLE veetee_ota_rollouts ADD COLUMN IF NOT EXISTS rollback_scope TEXT;
ALTER TABLE veetee_ota_rollouts ADD COLUMN IF NOT EXISTS rollback_device_id TEXT
    REFERENCES veetee_devices(device_id) ON DELETE CASCADE;
ALTER TABLE veetee_ota_rollouts ADD COLUMN IF NOT EXISTS rollback_cohort TEXT;
ALTER TABLE veetee_ota_rollouts DROP CONSTRAINT IF EXISTS veetee_ota_rollouts_kind_check;
ALTER TABLE veetee_ota_rollouts ADD CONSTRAINT veetee_ota_rollouts_kind_check
    CHECK (kind IN ('release', 'rollback'));
ALTER TABLE veetee_ota_rollouts DROP CONSTRAINT IF EXISTS veetee_ota_rollouts_rollback_scope_check;
ALTER TABLE veetee_ota_rollouts ADD CONSTRAINT veetee_ota_rollouts_rollback_scope_check
    CHECK (rollback_scope IN ('rollout', 'cohort', 'device'));
ALTER TABLE veetee_ota_rollouts DROP CONSTRAINT IF EXISTS veetee_ota_rollouts_scope_shape_check;
ALTER TABLE veetee_ota_rollouts ADD CONSTRAINT veetee_ota_rollouts_scope_shape_check CHECK (
    (kind = 'release' AND rollback_scope IS NULL
        AND rollback_device_id IS NULL AND rollback_cohort IS NULL)
    OR (kind = 'rollback' AND rollback_scope = 'rollout'
        AND rollback_device_id IS NULL AND rollback_cohort IS NULL)
    OR (kind = 'rollback' AND rollback_scope = 'device'
        AND rollback_device_id IS NOT NULL AND rollback_cohort IS NULL)
    OR (kind = 'rollback' AND rollback_scope = 'cohort'
        AND rollback_device_id IS NULL AND rollback_cohort IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS veetee_rollouts_release_status_idx
    ON veetee_ota_rollouts(release_id, status);

-- Evidence that discovery actually offered a release to this device.
CREATE TABLE IF NOT EXISTS veetee_ota_offers (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    release_id UUID NOT NULL REFERENCES veetee_ota_releases(id) ON DELETE CASCADE,
    offered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (device_id, release_id)
);

-- Explicit authorization is the only exception to normal anti-rollback eligibility.
CREATE TABLE IF NOT EXISTS veetee_ota_rollback_authorizations (
    id UUID PRIMARY KEY,
    source_rollout_id UUID NOT NULL REFERENCES veetee_ota_rollouts(id) ON DELETE RESTRICT,
    source_release_id UUID NOT NULL REFERENCES veetee_ota_releases(id) ON DELETE RESTRICT,
    target_release_id UUID NOT NULL REFERENCES veetee_ota_releases(id) ON DELETE RESTRICT,
    target_rollout_id UUID NOT NULL REFERENCES veetee_ota_rollouts(id) ON DELETE RESTRICT,
    scope TEXT NOT NULL CHECK (scope IN ('rollout', 'cohort', 'device')),
    device_id TEXT REFERENCES veetee_devices(device_id) ON DELETE CASCADE,
    cohort TEXT,
    created_by UUID NOT NULL REFERENCES veetee_users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    CHECK ((scope = 'device') = (device_id IS NOT NULL)),
    CHECK ((scope = 'cohort') = (cohort IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS veetee_rollback_authorization_lookup_idx
    ON veetee_ota_rollback_authorizations(target_release_id, device_id, cohort)
    WHERE revoked_at IS NULL;

-- Append-only idempotent OTA reports
CREATE TABLE IF NOT EXISTS veetee_ota_reports (
    id UUID PRIMARY KEY,
    event_id UUID NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    release_id UUID REFERENCES veetee_ota_releases(id) ON DELETE SET NULL,
    version TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL CHECK (stage IN ('check', 'download', 'install', 'boot', 'rollback')),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'skipped', 'in_progress')),
    error_message TEXT NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS veetee_ota_reports_device_idx
    ON veetee_ota_reports(device_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS veetee_ota_reports_terminal_once_idx
    ON veetee_ota_reports(device_id, release_id, stage)
    WHERE release_id IS NOT NULL AND outcome IN ('success', 'failure', 'skipped');

-- Artifacts, releases, and reports are append-only. Rollout state is the mutable switch.
CREATE OR REPLACE FUNCTION veetee_reject_immutable_change() RETURNS trigger AS $$
BEGIN
    IF TG_TABLE_NAME = 'veetee_ota_reports'
       AND TG_OP = 'DELETE'
       AND current_setting('veetee.allow_report_cleanup', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION veetee_restrict_release_change() RETURNS trigger AS $$
BEGIN
    IF OLD.is_published = false AND NEW.is_published = true
       AND NEW.id = OLD.id AND NEW.version = OLD.version
       AND NEW.artifact_id = OLD.artifact_id AND NEW.board = OLD.board
       AND NEW.chip = OLD.chip AND NEW.partition = OLD.partition
       AND NEW.channel = OLD.channel AND NEW.min_current_version = OLD.min_current_version
       AND NEW.provenance = OLD.provenance
       AND NEW.rollback_target_id IS NOT DISTINCT FROM OLD.rollback_target_id
       AND NEW.created_at = OLD.created_at AND NEW.published_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% is immutable except for first publish', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS veetee_ota_artifacts_immutable ON veetee_ota_artifacts;
CREATE TRIGGER veetee_ota_artifacts_immutable
    BEFORE UPDATE OR DELETE ON veetee_ota_artifacts
    FOR EACH ROW EXECUTE FUNCTION veetee_reject_immutable_change();
DROP TRIGGER IF EXISTS veetee_ota_releases_immutable ON veetee_ota_releases;
CREATE TRIGGER veetee_ota_releases_immutable
    BEFORE UPDATE OR DELETE ON veetee_ota_releases
    FOR EACH ROW EXECUTE FUNCTION veetee_restrict_release_change();
DROP TRIGGER IF EXISTS veetee_ota_reports_immutable ON veetee_ota_reports;
CREATE TRIGGER veetee_ota_reports_immutable
    BEFORE UPDATE OR DELETE ON veetee_ota_reports
    FOR EACH ROW EXECUTE FUNCTION veetee_reject_immutable_change();

-- Record schema migration
INSERT INTO veetee_schema_migrations(version)
VALUES ('003_device_lifecycle_ota')
ON CONFLICT (version) DO NOTHING;

COMMIT;
