BEGIN;

-- This preflight must run before changing the M4 tenant-scoped constraint. It
-- deliberately aborts the whole transaction and names the conflicting IDs.
DO $$
DECLARE
    duplicate_ids TEXT;
BEGIN
    SELECT string_agg(device_id, ', ' ORDER BY device_id)
    INTO duplicate_ids
    FROM (
        SELECT device_id
        FROM veetee_devices
        GROUP BY device_id
        HAVING count(*) > 1
        LIMIT 20
    ) duplicates;
    IF duplicate_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'M5 migration refused: duplicate global device_id values: %', duplicate_ids;
    END IF;
END $$;

ALTER TABLE veetee_devices
    DROP CONSTRAINT IF EXISTS veetee_devices_owner_user_id_device_id_key;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'veetee_devices'::regclass
          AND conname = 'veetee_devices_device_id_key'
    ) THEN
        ALTER TABLE veetee_devices
            ADD CONSTRAINT veetee_devices_device_id_key UNIQUE (device_id);
    END IF;
END $$;
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS board TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS chip TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS partition_name TEXT NOT NULL DEFAULT 'app';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS firmware_version TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS client_id TEXT NOT NULL DEFAULT '';
ALTER TABLE veetee_devices ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE veetee_devices DROP COLUMN IF EXISTS activation_code_hash;

CREATE TABLE IF NOT EXISTS veetee_device_activations (
    id UUID PRIMARY KEY,
    device_id TEXT NOT NULL UNIQUE,
    code TEXT NOT NULL UNIQUE CHECK (code ~ '^[0-9]{6}$'),
    challenge TEXT NOT NULL CHECK (challenge <> ''),
    client_id TEXT NOT NULL,
    board TEXT NOT NULL,
    chip TEXT NOT NULL,
    partition_name TEXT NOT NULL,
    firmware_version TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE veetee_device_activations DROP COLUMN IF EXISTS failed_attempts;

CREATE TABLE IF NOT EXISTS veetee_device_bind_attempts (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS veetee_device_bind_attempts_scope_idx
    ON veetee_device_bind_attempts(owner_user_id, attempted_at DESC);

CREATE TABLE IF NOT EXISTS veetee_device_bind_receipts (
    id UUID PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE CHECK (code_hash ~ '^[0-9a-f]{64}$'),
    device_id UUID NOT NULL REFERENCES veetee_devices(id) ON DELETE CASCADE,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE CASCADE,
    agent_id UUID NOT NULL REFERENCES veetee_agents(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS veetee_device_bind_receipts_expiry_idx
    ON veetee_device_bind_receipts(expires_at);

CREATE TABLE IF NOT EXISTS veetee_firmware_artifacts (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE RESTRICT,
    storage_name TEXT NOT NULL UNIQUE,
    file_size BIGINT NOT NULL CHECK (file_size > 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS veetee_firmware_releases (
    id UUID PRIMARY KEY,
    owner_user_id UUID NOT NULL REFERENCES veetee_users(id) ON DELETE RESTRICT,
    artifact_id UUID NOT NULL REFERENCES veetee_firmware_artifacts(id) ON DELETE RESTRICT,
    version TEXT NOT NULL CHECK (version <> ''),
    board TEXT NOT NULL CHECK (board <> ''),
    chip TEXT NOT NULL CHECK (chip <> ''),
    partition_name TEXT NOT NULL CHECK (partition_name <> ''),
    force BOOLEAN NOT NULL DEFAULT false,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE veetee_firmware_releases
    DROP CONSTRAINT IF EXISTS veetee_firmware_releases_version_board_chip_partition_name_key;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'veetee_firmware_releases'::regclass
          AND conname = 'veetee_firmware_releases_owner_compat_key'
    ) THEN
        ALTER TABLE veetee_firmware_releases
            ADD CONSTRAINT veetee_firmware_releases_owner_compat_key
            UNIQUE (owner_user_id, version, board, chip, partition_name);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS veetee_firmware_releases_compat_idx
    ON veetee_firmware_releases(owner_user_id, board, chip, partition_name, published_at);

INSERT INTO veetee_schema_migrations(version)
VALUES ('003_device_activation_ota')
ON CONFLICT (version) DO NOTHING;

COMMIT;
