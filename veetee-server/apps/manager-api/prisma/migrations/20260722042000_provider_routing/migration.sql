CREATE TYPE "ProviderCircuitState" AS ENUM ('CLOSED', 'OPEN', 'HALF_OPEN');

ALTER TABLE "ProviderBinding"
ADD COLUMN "priority" INTEGER NOT NULL DEFAULT 100,
ADD COLUMN "locales" TEXT[] NOT NULL DEFAULT ARRAY['*']::TEXT[],
ADD COLUMN "healthLatencyMs" INTEGER,
ADD COLUMN "healthErrorCode" TEXT,
ADD COLUMN "healthCheckedAt" TIMESTAMP(3),
ADD COLUMN "circuitState" "ProviderCircuitState" NOT NULL DEFAULT 'CLOSED',
ADD COLUMN "failureCount" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN "circuitOpenedAt" TIMESTAMP(3);

DROP INDEX "ProviderBinding_tenantId_kind_enabled_idx";
CREATE INDEX "ProviderBinding_tenantId_kind_enabled_priority_idx"
ON "ProviderBinding"("tenantId", "kind", "enabled", "priority");
