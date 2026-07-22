DROP INDEX IF EXISTS "Artifact_tenantId_kind_version_key";

CREATE TABLE "UiPackRollout" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "artifactId" TEXT NOT NULL,
    "status" "ResourceRolloutStatus" NOT NULL DEFAULT 'ACTIVE',
    "desiredStateVersion" INTEGER NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,
    CONSTRAINT "UiPackRollout_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "UiPackRollout_tenantId_createdAt_idx" ON "UiPackRollout"("tenantId", "createdAt");
CREATE INDEX "UiPackRollout_deviceId_status_idx" ON "UiPackRollout"("deviceId", "status");

ALTER TABLE "UiPackRollout" ADD CONSTRAINT "UiPackRollout_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "UiPackRollout" ADD CONSTRAINT "UiPackRollout_deviceId_fkey"
FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "UiPackRollout" ADD CONSTRAINT "UiPackRollout_artifactId_fkey"
FOREIGN KEY ("artifactId") REFERENCES "Artifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
