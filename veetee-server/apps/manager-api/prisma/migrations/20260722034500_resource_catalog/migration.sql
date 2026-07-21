CREATE TYPE "ArtifactKind" AS ENUM (
    'RESOURCE_BUNDLE',
    'MODEL_PACK',
    'DISPLAY_ASSETS',
    'AUDIO_ASSETS',
    'ADMISSION_MODEL'
);

CREATE TYPE "ArtifactStatus" AS ENUM ('VALIDATED', 'PUBLISHED', 'REVOKED');
CREATE TYPE "ArtifactBenchmarkStatus" AS ENUM ('NOT_RUN', 'PASSED', 'FAILED');
CREATE TYPE "ResourceRolloutStatus" AS ENUM ('ACTIVE', 'COMPLETE', 'FAILED', 'ROLLED_BACK');

CREATE TABLE "Artifact" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "kind" "ArtifactKind" NOT NULL,
    "version" TEXT NOT NULL,
    "channel" TEXT NOT NULL,
    "sizeBytes" INTEGER NOT NULL,
    "sha256" TEXT NOT NULL,
    "contentType" TEXT NOT NULL,
    "runtime" TEXT NOT NULL,
    "runtimeAbi" INTEGER NOT NULL,
    "license" TEXT NOT NULL,
    "board" TEXT NOT NULL,
    "minFirmware" TEXT NOT NULL,
    "maxFirmware" TEXT NOT NULL,
    "signatureKeyId" TEXT NOT NULL,
    "securityEpoch" INTEGER NOT NULL,
    "benchmarkStatus" "ArtifactBenchmarkStatus" NOT NULL DEFAULT 'NOT_RUN',
    "status" "ArtifactStatus" NOT NULL DEFAULT 'VALIDATED',
    "manifest" JSONB NOT NULL,
    "publishedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Artifact_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "WakeProfile" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "artifactId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "locale" TEXT NOT NULL,
    "channel" TEXT NOT NULL,
    "activationPhrase" TEXT NOT NULL,
    "activation" JSONB NOT NULL,
    "interrupt" JSONB NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 1,
    "publishedVersion" INTEGER NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "WakeProfile_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "WakeProfileVersion" (
    "id" TEXT NOT NULL,
    "wakeProfileId" TEXT NOT NULL,
    "artifactId" TEXT NOT NULL,
    "version" INTEGER NOT NULL,
    "snapshot" JSONB NOT NULL,
    "publishedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "WakeProfileVersion_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "ResourceRollout" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "artifactId" TEXT NOT NULL,
    "wakeProfileVersionId" TEXT NOT NULL,
    "status" "ResourceRolloutStatus" NOT NULL DEFAULT 'ACTIVE',
    "desiredStateVersion" INTEGER NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ResourceRollout_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "Artifact_tenantId_kind_version_key"
ON "Artifact"("tenantId", "kind", "version");
CREATE INDEX "Artifact_tenantId_status_createdAt_idx"
ON "Artifact"("tenantId", "status", "createdAt");
CREATE UNIQUE INDEX "WakeProfile_tenantId_name_key"
ON "WakeProfile"("tenantId", "name");
CREATE INDEX "WakeProfile_tenantId_updatedAt_idx"
ON "WakeProfile"("tenantId", "updatedAt");
CREATE UNIQUE INDEX "WakeProfileVersion_wakeProfileId_version_key"
ON "WakeProfileVersion"("wakeProfileId", "version");
CREATE INDEX "WakeProfileVersion_wakeProfileId_publishedAt_idx"
ON "WakeProfileVersion"("wakeProfileId", "publishedAt");
CREATE INDEX "ResourceRollout_tenantId_createdAt_idx"
ON "ResourceRollout"("tenantId", "createdAt");
CREATE INDEX "ResourceRollout_deviceId_status_idx"
ON "ResourceRollout"("deviceId", "status");

ALTER TABLE "Artifact" ADD CONSTRAINT "Artifact_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "WakeProfile" ADD CONSTRAINT "WakeProfile_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "WakeProfile" ADD CONSTRAINT "WakeProfile_artifactId_fkey"
FOREIGN KEY ("artifactId") REFERENCES "Artifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "WakeProfileVersion" ADD CONSTRAINT "WakeProfileVersion_wakeProfileId_fkey"
FOREIGN KEY ("wakeProfileId") REFERENCES "WakeProfile"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "WakeProfileVersion" ADD CONSTRAINT "WakeProfileVersion_artifactId_fkey"
FOREIGN KEY ("artifactId") REFERENCES "Artifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "ResourceRollout" ADD CONSTRAINT "ResourceRollout_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ResourceRollout" ADD CONSTRAINT "ResourceRollout_deviceId_fkey"
FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ResourceRollout" ADD CONSTRAINT "ResourceRollout_artifactId_fkey"
FOREIGN KEY ("artifactId") REFERENCES "Artifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "ResourceRollout" ADD CONSTRAINT "ResourceRollout_wakeProfileVersionId_fkey"
FOREIGN KEY ("wakeProfileVersionId") REFERENCES "WakeProfileVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
