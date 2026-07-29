import { ConflictException } from "@nestjs/common";
import { MemoryMessageRole, TenantRole } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import type { AuditService } from "../audit/audit.service.js";
import type { PrismaService } from "../database/prisma.service.js";
import { MemoryService } from "./memory.service.js";

const policy = {
  enabled: true,
  consent: true,
  storeMessages: true,
  storeFacts: true,
  retentionDays: 7,
  maxMessages: 12,
  maxMessageCharacters: 2_000,
  maxContextCharacters: 8_000,
  factRetentionDays: 90,
  maxFacts: 50,
  maxFactCharacters: 1_000,
};

function harness(
  memoryPolicy: Record<string, unknown> = policy,
  publishedMemoryPolicy: Record<string, unknown> = memoryPolicy,
  publishedVersion = 4,
) {
  const transaction = {
    $queryRaw: vi.fn().mockResolvedValue([{ id: "device-1" }]),
    $executeRaw: vi.fn().mockResolvedValue(0),
    agent: {
      findFirst: vi.fn().mockResolvedValue({ publishedVersion }),
    },
    agentConfigVersion: {
      findUnique: vi.fn().mockResolvedValue({
        snapshot: { memoryPolicy: publishedMemoryPolicy },
      }),
    },
    conversationMemoryMessage: {
      createMany: vi.fn().mockResolvedValue({ count: 1 }),
      deleteMany: vi.fn().mockResolvedValue({ count: 0 }),
      findMany: vi.fn().mockResolvedValue([]),
    },
    conversationMemoryFact: {
      deleteMany: vi.fn().mockResolvedValue({ count: 0 }),
      findMany: vi.fn().mockResolvedValue([]),
    },
    memoryWriteReceipt: {
      createMany: vi.fn().mockResolvedValue({ count: 1 }),
      deleteMany: vi.fn().mockResolvedValue({ count: 0 }),
      findMany: vi.fn().mockResolvedValue([]),
    },
  };
  const prisma = {
    agentConfigVersion: {
      findUnique: vi.fn().mockResolvedValue({
        agent: { tenantId: "tenant-1" },
        snapshot: { memoryPolicy },
      }),
    },
    device: { findFirst: vi.fn().mockResolvedValue({ id: "device-1" }) },
    $transaction: vi.fn(async (operation: (client: typeof transaction) => unknown) =>
      operation(transaction)),
  };
  const audit = { record: vi.fn().mockResolvedValue(undefined) };
  return {
    prisma,
    transaction,
    audit,
    service: new MemoryService(
      prisma as unknown as PrismaService,
      audit as unknown as AuditService,
    ),
  };
}

describe("MemoryService", () => {
  it("derives tenant from the immutable agent config and stores idempotent bounded text", async () => {
    const { service, prisma, transaction, audit } = harness();
    const result = await service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: "session-12345678:turn-12345678:user",
        sessionId: "session-12345678",
        turnId: "turn-12345678",
        role: "user",
        content: "Tôi thích trà sen.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    );
    expect(prisma.device.findFirst).toHaveBeenCalledWith({
      where: { id: "device-1", tenantId: "tenant-1", agentId: "agent-1" },
      select: { id: true },
    });
    expect(transaction.conversationMemoryMessage.createMany).toHaveBeenCalledWith(
      expect.objectContaining({
        skipDuplicates: true,
        data: [expect.objectContaining({
          tenantId: "tenant-1",
          role: MemoryMessageRole.USER,
          content: "Tôi thích trà sen.",
        })],
      }),
    );
    expect(audit.record).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "memory.messages.append",
        details: expect.not.objectContaining({ content: expect.anything() }),
      }),
      transaction,
    );
    expect(result).toEqual({ accepted: 1, duplicates: 0 });
  });

  it("deletes boundary-expired messages before receipt lookup and replacement create", async () => {
    const { service, transaction } = harness();
    transaction.conversationMemoryMessage.deleteMany.mockResolvedValueOnce({ count: 1 });
    await service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: "session-12345678:turn-boundary:user",
        sessionId: "session-12345678",
        turnId: "turn-boundary",
        role: "user",
        content: "Retry đúng lúc hết hạn.",
        occurredAt: new Date().toISOString(),
      }],
    );
    expect(
      transaction.conversationMemoryMessage.deleteMany.mock.invocationCallOrder[0],
    ).toBeLessThan(
      transaction.memoryWriteReceipt.findMany.mock.invocationCallOrder[0] ?? Number.MAX_VALUE,
    );
    expect(
      transaction.conversationMemoryMessage.deleteMany.mock.invocationCallOrder[0],
    ).toBeLessThan(
      transaction.conversationMemoryMessage.createMany.mock.invocationCallOrder[0]
        ?? Number.MAX_VALUE,
    );
  });

  it("fails closed when consent or message storage is disabled", async () => {
    const { service, transaction } = harness({
      ...policy,
      enabled: false,
      consent: false,
      storeMessages: false,
    });
    await expect(service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: "session-12345678:turn-12345678:user",
        sessionId: "session-12345678",
        turnId: "turn-12345678",
        role: "user",
        content: "Không được lưu.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    )).rejects.toBeInstanceOf(ConflictException);
    expect(transaction.conversationMemoryMessage.createMany).not.toHaveBeenCalled();
  });

  it("uses current published consent as a kill-switch for stale config versions", async () => {
    const revoked = { ...policy, enabled: false, consent: false };
    const { service, transaction, audit } = harness(policy, revoked, 5);
    await expect(service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: "session-12345678:turn-12345678:user",
        sessionId: "session-12345678",
        turnId: "turn-12345678",
        role: "user",
        content: "Không được ghi sau khi thu hồi.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    )).rejects.toBeInstanceOf(ConflictException);
    expect(transaction.conversationMemoryMessage.createMany).not.toHaveBeenCalled();
    await expect(service.getContext("agent-1", "device-1", 4)).resolves.toMatchObject({
      policy: { enabled: false, consent: false },
      messages: [],
      memoryFacts: [],
    });
    expect(transaction.conversationMemoryMessage.findMany).not.toHaveBeenCalled();
    expect(transaction.conversationMemoryFact.findMany).not.toHaveBeenCalled();
    expect(audit.record).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "memory.context.load",
        details: expect.objectContaining({ enabled: false, messageCount: 0, factCount: 0 }),
      }),
      transaction,
    );
  });

  it("keeps stale-version writes active after an unrelated publish with unchanged memory policy", async () => {
    const { service } = harness(policy, policy, 5);
    await expect(service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: "session-12345678:turn-12345678:user",
        sessionId: "session-12345678",
        turnId: "turn-12345678",
        role: "user",
        content: "Vẫn được ghi khi consent không đổi.",
        occurredAt: "2026-07-29T04:00:00.000Z",
      }],
    )).resolves.toEqual({ accepted: 1, duplicates: 0 });
  });

  it.each([
    ["past", "2020-01-01T00:00:00.000Z"],
    ["future", "2099-01-01T00:00:00.000Z"],
  ] as const)("bounds %s occurredAt retention by event and ingest time", async (_, occurredAt) => {
    const { service, transaction } = harness();
    const ingestStartedAt = Date.now();
    await service.appendMessages(
      "agent-1",
      "device-1",
      4,
      [{
        idempotencyKey: `session-12345678:turn-${occurredAt.slice(0, 4)}:user`,
        sessionId: "session-12345678",
        turnId: `turn-${occurredAt.slice(0, 4)}`,
        role: "user",
        content: "Giữ đúng thời hạn lưu.",
        occurredAt,
      }],
    );
    const stored = transaction.conversationMemoryMessage.createMany.mock.calls[0]![0]
      .data[0];
    const receipt = transaction.memoryWriteReceipt.createMany.mock.calls[0]![0]
      .data[0];
    const supplied = new Date(occurredAt).getTime();
    expect(stored.occurredAt.getTime()).toBe(Math.min(supplied, stored.occurredAt.getTime()));
    if (supplied > ingestStartedAt) {
      expect(stored.occurredAt.getTime()).toBeGreaterThanOrEqual(ingestStartedAt);
      expect(stored.occurredAt.getTime()).toBeLessThanOrEqual(Date.now());
    } else {
      expect(stored.occurredAt.toISOString()).toBe(occurredAt);
    }
    expect(stored.retentionUntil.getTime() - stored.occurredAt.getTime()).toBe(7 * 86_400_000);
    expect(receipt.expiresAt.getTime()).toBeGreaterThan(ingestStartedAt);
    if (supplied < ingestStartedAt - 7 * 86_400_000) {
      expect(stored.retentionUntil.getTime()).toBeLessThan(ingestStartedAt);
      expect(receipt.expiresAt.getTime()).toBeGreaterThan(stored.retentionUntil.getTime());
    }
  });

  it("runs bounded global expiry cleanup at lifecycle start", async () => {
    const transaction = {
      $queryRaw: vi.fn()
        .mockResolvedValueOnce([{ tenantId: "tenant-1" }])
        .mockResolvedValueOnce([{ tenantId: "tenant-1" }])
        .mockResolvedValueOnce([{ tenantId: "tenant-1" }]),
    };
    const prisma = {
      $transaction: vi.fn(async (operation: (client: typeof transaction) => unknown) =>
        operation(transaction)),
    };
    const audit = { record: vi.fn().mockResolvedValue(undefined) };
    const service = new MemoryService(
      prisma as unknown as PrismaService,
      audit as unknown as AuditService,
    );
    await service.onModuleInit();
    service.onModuleDestroy();
    expect(transaction.$queryRaw).toHaveBeenCalledTimes(3);
    expect(audit.record).toHaveBeenCalledWith(
      expect.objectContaining({
        tenantId: "tenant-1",
        action: "memory.retention.cleanup_global",
        details: {
          messagesDeleted: 1,
          factsDeleted: 1,
          receiptsDeleted: 1,
        },
      }),
      transaction,
    );
  });

  it("exports bounded active data with tenant/device scope and content-free audit", async () => {
    const now = new Date("2026-07-29T05:00:00.000Z");
    const message = {
      id: "message-1",
      sessionId: "session-12345678",
      turnId: "turn-12345678",
      role: MemoryMessageRole.USER,
      content: "Tôi thích trà sen.",
      occurredAt: now,
      retentionUntil: new Date("2026-08-01T05:00:00.000Z"),
    };
    const fact = {
      id: "fact-1",
      category: "preference",
      key: "favorite_drink",
      value: "Trà sen",
      confidence: 0.9,
      sourceSessionId: "session-12345678",
      sourceTurnId: "turn-12345678",
      expiresAt: new Date("2026-08-10T05:00:00.000Z"),
      updatedAt: now,
    };
    const transaction = {
      $queryRaw: vi.fn().mockResolvedValue([{ id: "device-1" }]),
      $executeRaw: vi.fn().mockResolvedValue(0),
      agent: {
        findFirst: vi.fn().mockResolvedValue({
          publishedVersion: 4,
          draftConfig: { memoryPolicy: policy },
        }),
      },
      agentConfigVersion: {
        findUnique: vi.fn().mockResolvedValue({ snapshot: { memoryPolicy: policy } }),
      },
      conversationMemoryMessage: { findMany: vi.fn().mockResolvedValue([message]) },
      conversationMemoryFact: { findMany: vi.fn().mockResolvedValue([fact]) },
    };
    const prisma = {
      $transaction: vi.fn(async (operation: (client: typeof transaction) => unknown) =>
        operation(transaction)),
    };
    const audit = { record: vi.fn().mockResolvedValue(undefined) };
    const service = new MemoryService(
      prisma as unknown as PrismaService,
      audit as unknown as AuditService,
    );
    const principal = {
      userId: "user-1",
      tenantId: "tenant-1",
      tenantSlug: "tenant",
      role: TenantRole.OPERATOR,
      email: "operator@example.test",
      displayName: "Operator",
    };
    const exported = await service.exportMemory("agent-1", "device-1", {
      principal,
      requestId: "request-export",
    });
    expect(transaction.$queryRaw).toHaveBeenCalledTimes(2);
    expect(transaction.conversationMemoryMessage.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({
          tenantId: "tenant-1",
          agentId: "agent-1",
          deviceId: "device-1",
        }),
        take: 12,
      }),
    );
    expect(transaction.conversationMemoryFact.findMany).toHaveBeenCalledWith(
      expect.objectContaining({ take: 50 }),
    );
    expect(exported).toMatchObject({
      version: 1,
      agentId: "agent-1",
      deviceId: "device-1",
      messages: [expect.objectContaining({ content: "Tôi thích trà sen." })],
      facts: [expect.objectContaining({ value: "Trà sen" })],
    });
    expect(audit.record).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "memory.export.create",
        details: {
          agentId: "agent-1",
          messageCount: 1,
          factCount: 1,
          messageCharacters: 18,
          factCharacters: 7,
        },
      }),
      transaction,
    );
    expect(JSON.stringify(audit.record.mock.calls)).not.toContain("Trà sen");
  });
});
