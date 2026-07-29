-- CreateEnum
CREATE TYPE "MemoryMessageRole" AS ENUM ('USER', 'ASSISTANT');

-- CreateEnum
CREATE TYPE "MemoryWriteKind" AS ENUM ('MESSAGE', 'FACT');

-- CreateEnum
CREATE TYPE "RemoteMcpTransport" AS ENUM ('STREAMABLE_HTTP', 'SSE');

-- CreateEnum
CREATE TYPE "RemoteMcpAuthType" AS ENUM ('NONE', 'BEARER', 'HEADER');

-- CreateEnum
CREATE TYPE "RemoteMcpNetworkPolicy" AS ENUM ('PUBLIC_ONLY', 'PRIVATE_ALLOWLIST');

-- CreateEnum
CREATE TYPE "RemoteMcpHealth" AS ENUM ('UNKNOWN', 'HEALTHY', 'DEGRADED');

-- CreateEnum
CREATE TYPE "RemoteMcpCallStatus" AS ENUM ('SUCCEEDED', 'FAILED', 'CANCELLED', 'STALE', 'COMPLETED_AFTER_ABORT');

-- CreateEnum
CREATE TYPE "RemoteMcpCallActor" AS ENUM ('MODEL', 'USER', 'SYSTEM');

-- CreateTable
CREATE TABLE "ConversationMemoryMessage" (
    "id" TEXT NOT NULL,
    "idempotencyKey" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "agentId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "sessionId" TEXT NOT NULL,
    "turnId" TEXT NOT NULL,
    "role" "MemoryMessageRole" NOT NULL,
    "content" TEXT NOT NULL,
    "redacted" BOOLEAN NOT NULL DEFAULT false,
    "occurredAt" TIMESTAMP(3) NOT NULL,
    "retentionUntil" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ConversationMemoryMessage_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ConversationMemoryFact" (
    "id" TEXT NOT NULL,
    "lastIdempotencyKey" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "agentId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "category" TEXT NOT NULL,
    "key" TEXT NOT NULL,
    "value" TEXT NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "sourceSessionId" TEXT NOT NULL,
    "sourceTurnId" TEXT NOT NULL,
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "ConversationMemoryFact_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "MemoryWriteReceipt" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "agentId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "idempotencyKey" TEXT NOT NULL,
    "kind" "MemoryWriteKind" NOT NULL,
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "MemoryWriteReceipt_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RemoteMcpEndpoint" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "url" TEXT NOT NULL,
    "transport" "RemoteMcpTransport" NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "authType" "RemoteMcpAuthType" NOT NULL DEFAULT 'NONE',
    "authHeaderName" TEXT,
    "secretCiphertext" TEXT,
    "secretConfigured" BOOLEAN NOT NULL DEFAULT false,
    "timeoutMs" INTEGER NOT NULL DEFAULT 10000,
    "resultMaxBytes" INTEGER NOT NULL DEFAULT 65536,
    "networkPolicy" "RemoteMcpNetworkPolicy" NOT NULL DEFAULT 'PUBLIC_ONLY',
    "allowedHosts" TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    "tools" JSONB NOT NULL,
    "health" "RemoteMcpHealth" NOT NULL DEFAULT 'UNKNOWN',
    "healthLatencyMs" INTEGER,
    "healthErrorCode" TEXT,
    "healthCheckedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "RemoteMcpEndpoint_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AgentRemoteMcpAssignment" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "agentId" TEXT NOT NULL,
    "endpointId" TEXT NOT NULL,
    "toolNames" TEXT[] NOT NULL,
    "timeoutMs" INTEGER NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "AgentRemoteMcpAssignment_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "RemoteMcpInvocation" (
    "id" TEXT NOT NULL,
    "tenantId" TEXT NOT NULL,
    "endpointId" TEXT NOT NULL,
    "agentId" TEXT NOT NULL,
    "deviceId" TEXT NOT NULL,
    "configVersion" INTEGER NOT NULL,
    "sessionId" TEXT NOT NULL,
    "turnId" TEXT NOT NULL,
    "toolName" TEXT NOT NULL,
    "argumentsHash" TEXT NOT NULL,
    "status" "RemoteMcpCallStatus" NOT NULL,
    "durationMs" INTEGER NOT NULL,
    "actor" "RemoteMcpCallActor" NOT NULL,
    "occurredAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "RemoteMcpInvocation_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "Agent_tenantId_id_key" ON "Agent"("tenantId", "id");

-- CreateIndex
CREATE UNIQUE INDEX "Device_tenantId_id_key" ON "Device"("tenantId", "id");

-- CreateIndex
CREATE UNIQUE INDEX "ConversationMemoryMessage_tenantId_agentId_deviceId_idempot_key" ON "ConversationMemoryMessage"("tenantId", "agentId", "deviceId", "idempotencyKey");
CREATE INDEX "ConversationMemoryMessage_tenantId_agentId_deviceId_occurre_idx" ON "ConversationMemoryMessage"("tenantId", "agentId", "deviceId", "occurredAt");
CREATE INDEX "ConversationMemoryMessage_retentionUntil_idx" ON "ConversationMemoryMessage"("retentionUntil");

-- CreateIndex
CREATE INDEX "ConversationMemoryFact_tenantId_agentId_deviceId_lastIdempo_idx" ON "ConversationMemoryFact"("tenantId", "agentId", "deviceId", "lastIdempotencyKey");
CREATE UNIQUE INDEX "ConversationMemoryFact_tenantId_agentId_deviceId_category_k_key" ON "ConversationMemoryFact"("tenantId", "agentId", "deviceId", "category", "key");
CREATE INDEX "ConversationMemoryFact_tenantId_agentId_deviceId_updatedAt_idx" ON "ConversationMemoryFact"("tenantId", "agentId", "deviceId", "updatedAt");
CREATE INDEX "ConversationMemoryFact_expiresAt_idx" ON "ConversationMemoryFact"("expiresAt");

-- CreateIndex
CREATE UNIQUE INDEX "MemoryWriteReceipt_tenantId_agentId_deviceId_idempotencyKey_key" ON "MemoryWriteReceipt"("tenantId", "agentId", "deviceId", "idempotencyKey");
CREATE INDEX "MemoryWriteReceipt_tenantId_agentId_deviceId_expiresAt_idx" ON "MemoryWriteReceipt"("tenantId", "agentId", "deviceId", "expiresAt");
CREATE INDEX "MemoryWriteReceipt_expiresAt_idx" ON "MemoryWriteReceipt"("expiresAt");

-- CreateIndex
CREATE UNIQUE INDEX "RemoteMcpEndpoint_tenantId_name_key" ON "RemoteMcpEndpoint"("tenantId", "name");
CREATE UNIQUE INDEX "RemoteMcpEndpoint_tenantId_id_key" ON "RemoteMcpEndpoint"("tenantId", "id");
CREATE INDEX "RemoteMcpEndpoint_tenantId_enabled_updatedAt_idx" ON "RemoteMcpEndpoint"("tenantId", "enabled", "updatedAt");

-- CreateIndex
CREATE UNIQUE INDEX "AgentRemoteMcpAssignment_agentId_endpointId_key" ON "AgentRemoteMcpAssignment"("agentId", "endpointId");
CREATE INDEX "AgentRemoteMcpAssignment_tenantId_agentId_idx" ON "AgentRemoteMcpAssignment"("tenantId", "agentId");
CREATE INDEX "AgentRemoteMcpAssignment_endpointId_idx" ON "AgentRemoteMcpAssignment"("endpointId");

-- CreateIndex
CREATE INDEX "RemoteMcpInvocation_tenantId_occurredAt_idx" ON "RemoteMcpInvocation"("tenantId", "occurredAt");
CREATE INDEX "RemoteMcpInvocation_endpointId_occurredAt_idx" ON "RemoteMcpInvocation"("endpointId", "occurredAt");
CREATE INDEX "RemoteMcpInvocation_deviceId_sessionId_turnId_idx" ON "RemoteMcpInvocation"("deviceId", "sessionId", "turnId");

-- AddForeignKey
ALTER TABLE "ConversationMemoryMessage" ADD CONSTRAINT "ConversationMemoryMessage_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ConversationMemoryMessage" ADD CONSTRAINT "ConversationMemoryMessage_tenantId_agentId_fkey" FOREIGN KEY ("tenantId", "agentId") REFERENCES "Agent"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ConversationMemoryMessage" ADD CONSTRAINT "ConversationMemoryMessage_tenantId_deviceId_fkey" FOREIGN KEY ("tenantId", "deviceId") REFERENCES "Device"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ConversationMemoryFact" ADD CONSTRAINT "ConversationMemoryFact_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ConversationMemoryFact" ADD CONSTRAINT "ConversationMemoryFact_tenantId_agentId_fkey" FOREIGN KEY ("tenantId", "agentId") REFERENCES "Agent"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ConversationMemoryFact" ADD CONSTRAINT "ConversationMemoryFact_tenantId_deviceId_fkey" FOREIGN KEY ("tenantId", "deviceId") REFERENCES "Device"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "MemoryWriteReceipt" ADD CONSTRAINT "MemoryWriteReceipt_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MemoryWriteReceipt" ADD CONSTRAINT "MemoryWriteReceipt_tenantId_agentId_fkey" FOREIGN KEY ("tenantId", "agentId") REFERENCES "Agent"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MemoryWriteReceipt" ADD CONSTRAINT "MemoryWriteReceipt_tenantId_deviceId_fkey" FOREIGN KEY ("tenantId", "deviceId") REFERENCES "Device"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RemoteMcpEndpoint" ADD CONSTRAINT "RemoteMcpEndpoint_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AgentRemoteMcpAssignment" ADD CONSTRAINT "AgentRemoteMcpAssignment_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "AgentRemoteMcpAssignment" ADD CONSTRAINT "AgentRemoteMcpAssignment_tenantId_agentId_fkey" FOREIGN KEY ("tenantId", "agentId") REFERENCES "Agent"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "AgentRemoteMcpAssignment" ADD CONSTRAINT "AgentRemoteMcpAssignment_tenantId_endpointId_fkey" FOREIGN KEY ("tenantId", "endpointId") REFERENCES "RemoteMcpEndpoint"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "RemoteMcpInvocation" ADD CONSTRAINT "RemoteMcpInvocation_tenantId_fkey" FOREIGN KEY ("tenantId") REFERENCES "Tenant"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "RemoteMcpInvocation" ADD CONSTRAINT "RemoteMcpInvocation_tenantId_endpointId_fkey" FOREIGN KEY ("tenantId", "endpointId") REFERENCES "RemoteMcpEndpoint"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "RemoteMcpInvocation" ADD CONSTRAINT "RemoteMcpInvocation_tenantId_agentId_fkey" FOREIGN KEY ("tenantId", "agentId") REFERENCES "Agent"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "RemoteMcpInvocation" ADD CONSTRAINT "RemoteMcpInvocation_tenantId_deviceId_fkey" FOREIGN KEY ("tenantId", "deviceId") REFERENCES "Device"("tenantId", "id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "RemoteMcpInvocation" ADD CONSTRAINT "RemoteMcpInvocation_agentId_configVersion_fkey" FOREIGN KEY ("agentId", "configVersion") REFERENCES "AgentConfigVersion"("agentId", "version") ON DELETE CASCADE ON UPDATE CASCADE;
