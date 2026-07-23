ALTER TYPE "ArtifactKind" ADD VALUE IF NOT EXISTS 'FIRMWARE';

CREATE TYPE "FirmwareRolloutStatus" AS ENUM (
    'DRAFT',
    'RUNNING',
    'PAUSED',
    'COMPLETED',
    'FAILED',
    'ROLLED_BACK'
);

CREATE TABLE "FirmwareRollout" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "artifactId" TEXT NOT NULL,
    "previousArtifactId" TEXT,
    "channel" TEXT NOT NULL,
    "percentage" INTEGER NOT NULL DEFAULT 0,
    "canaryDeviceIds" TEXT[] DEFAULT ARRAY[]::TEXT[],
    "status" "FirmwareRolloutStatus" NOT NULL DEFAULT 'DRAFT',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "FirmwareRollout_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "FirmwareRollout_percentage_check"
      CHECK ("percentage" >= 0 AND "percentage" <= 100)
);

CREATE INDEX "FirmwareRollout_tenantId_createdAt_idx"
ON "FirmwareRollout"("tenantId", "createdAt");

CREATE INDEX "FirmwareRollout_tenantId_status_idx"
ON "FirmwareRollout"("tenantId", "status");

ALTER TABLE "FirmwareRollout" ADD CONSTRAINT "FirmwareRollout_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "FirmwareRollout" ADD CONSTRAINT "FirmwareRollout_artifactId_fkey"
FOREIGN KEY ("artifactId") REFERENCES "Artifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

ALTER TABLE "FirmwareRollout" ADD CONSTRAINT "FirmwareRollout_previousArtifactId_fkey"
FOREIGN KEY ("previousArtifactId") REFERENCES "Artifact"("id") ON DELETE SET NULL ON UPDATE CASCADE;
