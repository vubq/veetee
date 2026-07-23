CREATE TABLE "PersonalityPreset" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "label" TEXT NOT NULL,
    "summary" TEXT NOT NULL,
    "accent" TEXT NOT NULL,
    "instructions" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "PersonalityPreset_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "PersonalityPreset_tenantId_label_key" ON "PersonalityPreset"("tenantId", "label");
CREATE INDEX "PersonalityPreset_tenantId_createdAt_idx" ON "PersonalityPreset"("tenantId", "createdAt");

ALTER TABLE "PersonalityPreset" ADD CONSTRAINT "PersonalityPreset_tenantId_fkey"
  FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
