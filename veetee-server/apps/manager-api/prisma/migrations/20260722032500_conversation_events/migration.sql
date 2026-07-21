CREATE TABLE "ConversationEvent" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "agentId" TEXT,
    "sessionId" TEXT NOT NULL,
    "turnId" TEXT,
    "generation" INTEGER NOT NULL,
    "eventType" TEXT NOT NULL,
    "payload" JSONB NOT NULL DEFAULT '{}',
    "occurredAt" TIMESTAMP(3) NOT NULL,
    "retentionUntil" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ConversationEvent_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "ConversationEvent_tenantId_occurredAt_idx"
ON "ConversationEvent"("tenantId", "occurredAt");

CREATE INDEX "ConversationEvent_deviceId_occurredAt_idx"
ON "ConversationEvent"("deviceId", "occurredAt");

CREATE INDEX "ConversationEvent_sessionId_occurredAt_idx"
ON "ConversationEvent"("sessionId", "occurredAt");

CREATE INDEX "ConversationEvent_retentionUntil_idx"
ON "ConversationEvent"("retentionUntil");

ALTER TABLE "ConversationEvent"
ADD CONSTRAINT "ConversationEvent_tenantId_fkey"
FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "ConversationEvent"
ADD CONSTRAINT "ConversationEvent_deviceId_fkey"
FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE CASCADE ON UPDATE CASCADE;
