import { Buffer } from "node:buffer";

import {
  BadRequestException,
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  type OnModuleDestroy,
  type OnModuleInit,
} from "@nestjs/common";
import { MemoryMessageRole, MemoryWriteKind, Prisma } from "@prisma/client";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import {
  normalizeMemoryPolicy,
  type MemoryPolicy,
} from "../config/agent-config.policy.js";
import { PrismaService } from "../database/prisma.service.js";
import { reconcileMemoryPolicy } from "./memory-retention.js";

const DAY_MS = 24 * 60 * 60 * 1_000;
const SESSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:_-]{7,159}$/;
const IDEMPOTENCY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:._-]{7,199}$/;
const CATEGORY_PATTERN = /^[a-z][a-z0-9_.-]{0,63}$/;
const RETENTION_CLEANUP_INTERVAL_MS = 5 * 60 * 1_000;
const RETENTION_CLEANUP_BATCH = 500;
const MEMORY_EXPORT_MAX_MESSAGES = 40;
const MEMORY_EXPORT_MAX_FACTS = 100;
const MEMORY_EXPORT_MAX_MESSAGE_CHARACTERS = 4_000;
const MEMORY_EXPORT_MAX_FACT_CHARACTERS = 2_000;

export interface MemoryMessageInput {
  idempotencyKey: string;
  sessionId: string;
  turnId: string;
  role: "user" | "assistant";
  content: string;
  occurredAt: string;
}

export interface MemoryFactInput {
  idempotencyKey: string;
  category: string;
  key: string;
  value: string;
  confidence: number;
  sourceSessionId: string;
  sourceTurnId: string;
  expiresInDays: number;
}

export interface MemoryMessageRecord {
  id: string;
  sessionId: string;
  turnId: string;
  role: "user" | "assistant";
  content: string;
  occurredAt: string;
  retentionUntil: string;
}

export interface MemoryFactRecord {
  id: string;
  category: string;
  key: string;
  value: string;
  confidence: number;
  sourceSessionId: string;
  sourceTurnId: string;
  expiresAt: string;
  updatedAt: string;
}

export interface MemoryPage<T> {
  items: T[];
  nextCursor?: string;
}

export interface MemoryExportRecord {
  version: 1;
  exportedAt: string;
  agentId: string;
  deviceId: string;
  messages: MemoryMessageRecord[];
  facts: MemoryFactRecord[];
}

interface MemoryMutationContext {
  principal: Principal;
  requestId: string;
}

interface InternalScope {
  tenantId: string;
  agentId: string;
  deviceId: string;
  configVersion: number;
  policy: MemoryPolicy;
}

interface CursorValue {
  at: string;
  id: string;
}

@Injectable()
export class MemoryService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(MemoryService.name);
  private retentionTimer: NodeJS.Timeout | undefined;

  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.cleanupExpiredGlobal();
    this.retentionTimer = setInterval(() => {
      void this.cleanupExpiredGlobal().catch((error: unknown) => {
        this.logger.error(
          "Cross-session memory retention cleanup failed",
          error instanceof Error ? error.stack : undefined,
        );
      });
    }, RETENTION_CLEANUP_INTERVAL_MS);
    this.retentionTimer.unref();
  }

  onModuleDestroy(): void {
    if (this.retentionTimer) clearInterval(this.retentionTimer);
    this.retentionTimer = undefined;
  }

  async getContext(
    agentId: string,
    deviceId: string,
    configVersion: number,
  ): Promise<{
    policy: MemoryPolicy;
    messages: MemoryMessageRecord[];
    memoryFacts: MemoryFactRecord[];
  }> {
    const requestedScope = await this.internalScope(agentId, deviceId, configVersion);
    await this.cleanupExpired(requestedScope);
    return this.prisma.$transaction(async (transaction) => {
      const scope = await this.effectivePublishedScope(requestedScope, transaction);
      if (!scope.policy.enabled || !scope.policy.consent) {
        const result = { policy: scope.policy, messages: [], memoryFacts: [] };
        await this.auditMemoryContextLoad(scope, 0, 0, transaction);
        return result;
      }
      await reconcileMemoryPolicy(transaction, scope, scope.policy);
      const [rawMessages, rawFacts] = await Promise.all([
        scope.policy.storeMessages
          ? transaction.conversationMemoryMessage.findMany({
              where: {
                tenantId: scope.tenantId,
                agentId,
                deviceId,
                retentionUntil: { gt: new Date() },
                redacted: false,
              },
              orderBy: [{ occurredAt: "desc" }, { id: "desc" }],
              take: scope.policy.maxMessages,
            })
          : [],
        scope.policy.storeFacts
          ? transaction.conversationMemoryFact.findMany({
              where: {
                tenantId: scope.tenantId,
                agentId,
                deviceId,
                expiresAt: { gt: new Date() },
              },
              orderBy: [{ updatedAt: "desc" }, { id: "desc" }],
              take: scope.policy.maxFacts,
            })
          : [],
      ]);

      let remainingCharacters = scope.policy.maxContextCharacters;
      const memoryFacts: MemoryFactRecord[] = [];
      for (const fact of rawFacts) {
        if (remainingCharacters <= 0) break;
        const metadataCharacters = fact.category.length + fact.key.length;
        const availableValueCharacters = Math.min(
          scope.policy.maxFactCharacters,
          remainingCharacters - metadataCharacters,
        );
        if (availableValueCharacters <= 0) continue;
        const value = fact.value.slice(0, availableValueCharacters).trim();
        if (!value) continue;
        remainingCharacters -= metadataCharacters + value.length;
        memoryFacts.push(this.factRecord(fact, value));
      }
      const messages: MemoryMessageRecord[] = [];
      for (const message of rawMessages) {
        if (remainingCharacters <= 0) break;
        const content = message.content
          .slice(0, Math.min(scope.policy.maxMessageCharacters, remainingCharacters))
          .trim();
        if (!content) continue;
        remainingCharacters -= content.length;
        messages.push(this.messageRecord(message, content));
      }

      const result = {
        policy: scope.policy,
        messages: messages.reverse(),
        memoryFacts: memoryFacts.reverse(),
      };
      await this.auditMemoryContextLoad(
        scope,
        result.messages.length,
        result.memoryFacts.length,
        transaction,
      );
      return result;
    });
  }

  async appendMessages(
    agentId: string,
    deviceId: string,
    configVersion: number,
    messages: readonly MemoryMessageInput[],
  ): Promise<{ accepted: number; duplicates: number }> {
    const scope = await this.internalScope(agentId, deviceId, configVersion);
    this.requireMemoryStore(scope.policy, "messages");
    for (const message of messages) {
      if (!IDEMPOTENCY_PATTERN.test(message.idempotencyKey)) {
        throw new BadRequestException("Memory message idempotency key is invalid");
      }
      if (!SESSION_PATTERN.test(message.sessionId) || !SESSION_PATTERN.test(message.turnId)) {
        throw new BadRequestException("Memory message session or turn id is invalid");
      }
      if (message.role !== "user" && message.role !== "assistant") {
        throw new BadRequestException("Memory message role is invalid");
      }
      if (message.content.trim().length === 0) {
        throw new BadRequestException("Memory message content cannot be empty");
      }
      this.rejectUnsafeText(message.content, "Memory message content");
      if (message.content.length > scope.policy.maxMessageCharacters) {
        throw new BadRequestException(
          `Memory message exceeds policy limit ${scope.policy.maxMessageCharacters}`,
        );
      }
      if (!Number.isFinite(new Date(message.occurredAt).getTime())) {
        throw new BadRequestException("Memory message occurredAt is invalid");
      }
    }
    const now = new Date();
    return this.prisma.$transaction(async (transaction) => {
      const effective = await this.effectivePublishedScope(scope, transaction);
      this.requireMemoryStore(effective.policy, "messages");
      await reconcileMemoryPolicy(transaction, effective, effective.policy);
      for (const message of messages) {
        if (message.content.length > effective.policy.maxMessageCharacters) {
          throw new BadRequestException(
            `Memory message exceeds current policy limit ${effective.policy.maxMessageCharacters}`,
          );
        }
      }
      const receiptExpiresAt = new Date(
        now.getTime() + effective.policy.retentionDays * DAY_MS,
      );
      // Delete expired rows before checking receipts/creating replacements. Otherwise a
      // retry exactly at the boundary can hit the message unique key, report count=0 and
      // roll back before the old row is removed.
      const expired = await transaction.conversationMemoryMessage.deleteMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          retentionUntil: { lte: now },
        },
      });
      const expiredReceipts = await transaction.memoryWriteReceipt.deleteMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          expiresAt: { lte: now },
        },
      });
      const storedReceipts = await transaction.memoryWriteReceipt.findMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          idempotencyKey: { in: messages.map(({ idempotencyKey }) => idempotencyKey) },
        },
        select: { idempotencyKey: true },
      });
      const seen = new Set(storedReceipts.map(({ idempotencyKey }) => idempotencyKey));
      const pending = messages.filter(({ idempotencyKey }) => {
        if (seen.has(idempotencyKey)) return false;
        seen.add(idempotencyKey);
        return true;
      });
      const result = pending.length
        ? await transaction.conversationMemoryMessage.createMany({
            data: pending.map((message) => {
              const suppliedOccurredAt = new Date(message.occurredAt).getTime();
              const occurredAt = new Date(Math.min(suppliedOccurredAt, now.getTime()));
              return {
                idempotencyKey: message.idempotencyKey,
                tenantId: scope.tenantId,
                agentId,
                deviceId,
                sessionId: message.sessionId,
                turnId: message.turnId,
                role: message.role === "user"
                  ? MemoryMessageRole.USER
                  : MemoryMessageRole.ASSISTANT,
                content: message.content.trim(),
                occurredAt,
                retentionUntil: new Date(
                  occurredAt.getTime() + effective.policy.retentionDays * DAY_MS,
                ),
              };
            }),
            skipDuplicates: true,
          })
        : { count: 0 };
      if (result.count !== pending.length) {
        throw new ConflictException("Memory message idempotency state is inconsistent");
      }
      if (pending.length) {
        await transaction.memoryWriteReceipt.createMany({
          data: pending.map((message) => ({
            tenantId: scope.tenantId,
            agentId,
            deviceId,
            idempotencyKey: message.idempotencyKey,
            kind: MemoryWriteKind.MESSAGE,
            expiresAt: receiptExpiresAt,
          })),
        });
      }
      const newlyExpired = await transaction.conversationMemoryMessage.deleteMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          retentionUntil: { lte: now },
        },
      });
      const overflow = await transaction.conversationMemoryMessage.findMany({
        where: { tenantId: scope.tenantId, agentId, deviceId },
        orderBy: [{ occurredAt: "desc" }, { id: "desc" }],
        skip: effective.policy.maxMessages,
        select: { id: true },
      });
      const trimmed = overflow.length
        ? await transaction.conversationMemoryMessage.deleteMany({
            where: { id: { in: overflow.map(({ id }) => id) } },
          })
        : { count: 0 };
      const duplicateCount = messages.length - pending.length;
      if (
        result.count > 0 ||
        expired.count + newlyExpired.count > 0 ||
        expiredReceipts.count > 0 ||
        trimmed.count > 0
      ) {
        await this.audit.record(
          {
            tenantId: scope.tenantId,
            action: "memory.messages.append",
            targetType: "device_memory",
            targetId: deviceId,
            requestId: `memory:${messages[0]?.idempotencyKey ?? now.toISOString()}`,
            details: {
              agentId,
              configVersion,
              accepted: result.count,
              duplicates: duplicateCount,
              expiredDeleted: expired.count + newlyExpired.count,
              expiredReceiptsDeleted: expiredReceipts.count,
              historyTrimmed: trimmed.count,
            },
          },
          transaction,
        );
      }
      return { accepted: result.count, duplicates: duplicateCount };
    });
  }

  async upsertFacts(
    agentId: string,
    deviceId: string,
    configVersion: number,
    facts: readonly MemoryFactInput[],
  ): Promise<{ accepted: number; duplicates: number; rejected: number }> {
    const scope = await this.internalScope(agentId, deviceId, configVersion);
    this.requireMemoryStore(scope.policy, "facts");
    for (const fact of facts) {
      if (!IDEMPOTENCY_PATTERN.test(fact.idempotencyKey)) {
        throw new BadRequestException("Memory fact idempotency key is invalid");
      }
      if (!CATEGORY_PATTERN.test(fact.category)) {
        throw new BadRequestException("Memory fact category is invalid");
      }
      if (!fact.key.trim() || fact.key.length > 120) {
        throw new BadRequestException("Memory fact key must contain 1 to 120 characters");
      }
      this.rejectUnsafeText(fact.key, "Memory fact key");
      if (
        !SESSION_PATTERN.test(fact.sourceSessionId) ||
        !SESSION_PATTERN.test(fact.sourceTurnId)
      ) {
        throw new BadRequestException("Memory fact source session or turn id is invalid");
      }
      if (!Number.isFinite(fact.confidence) || fact.confidence < 0 || fact.confidence > 1) {
        throw new BadRequestException("Memory fact confidence must be between 0 and 1");
      }
      if (!Number.isInteger(fact.expiresInDays) || fact.expiresInDays < 1) {
        throw new BadRequestException("Memory fact expiresInDays must be a positive integer");
      }
      if (!fact.value.trim()) throw new BadRequestException("Memory fact value cannot be empty");
      this.rejectUnsafeText(fact.value, "Memory fact value");
      if (fact.value.length > scope.policy.maxFactCharacters) {
        throw new BadRequestException(
          `Memory fact exceeds policy limit ${scope.policy.maxFactCharacters}`,
        );
      }
      if (fact.expiresInDays > scope.policy.factRetentionDays) {
        throw new BadRequestException(
          `Memory fact expiry exceeds policy limit ${scope.policy.factRetentionDays}`,
        );
      }
    }

    return this.prisma.$transaction(async (transaction) => {
      const now = new Date();
      const effective = await this.effectivePublishedScope(scope, transaction);
      this.requireMemoryStore(effective.policy, "facts");
      await reconcileMemoryPolicy(transaction, effective, effective.policy);
      for (const fact of facts) {
        if (fact.value.length > effective.policy.maxFactCharacters) {
          throw new BadRequestException(
            `Memory fact exceeds current policy limit ${effective.policy.maxFactCharacters}`,
          );
        }
        if (fact.expiresInDays > effective.policy.factRetentionDays) {
          throw new BadRequestException(
            `Memory fact expiry exceeds current policy limit ${effective.policy.factRetentionDays}`,
          );
        }
      }
      const expiredReceipts = await transaction.memoryWriteReceipt.deleteMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          expiresAt: { lte: now },
        },
      });
      const expired = await transaction.conversationMemoryFact.deleteMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          expiresAt: { lte: now },
        },
      });
      let factCount = await transaction.conversationMemoryFact.count({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          expiresAt: { gt: now },
        },
      });
      let accepted = 0;
      let duplicates = 0;
      let rejected = 0;
      const storedReceipts = await transaction.memoryWriteReceipt.findMany({
        where: {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          idempotencyKey: { in: facts.map(({ idempotencyKey }) => idempotencyKey) },
        },
        select: { idempotencyKey: true },
      });
      const seenReceipts = new Set(
        storedReceipts.map(({ idempotencyKey }) => idempotencyKey),
      );
      for (const fact of facts) {
        if (seenReceipts.has(fact.idempotencyKey)) {
          duplicates += 1;
          continue;
        }
        seenReceipts.add(fact.idempotencyKey);
        const identity = {
          tenantId: scope.tenantId,
          agentId,
          deviceId,
          category: fact.category,
          key: fact.key.trim(),
        };
        const current = await transaction.conversationMemoryFact.findUnique({
          where: { tenantId_agentId_deviceId_category_key: identity },
          select: { id: true },
        });
        if (!current && factCount >= effective.policy.maxFacts) {
          rejected += 1;
          continue;
        }
        const expiresAt = new Date(now.getTime() + fact.expiresInDays * DAY_MS);
        await transaction.conversationMemoryFact.upsert({
          where: { tenantId_agentId_deviceId_category_key: identity },
          create: {
            ...identity,
            lastIdempotencyKey: fact.idempotencyKey,
            value: fact.value.trim(),
            confidence: fact.confidence,
            sourceSessionId: fact.sourceSessionId,
            sourceTurnId: fact.sourceTurnId,
            expiresAt,
          },
          update: {
            lastIdempotencyKey: fact.idempotencyKey,
            value: fact.value.trim(),
            confidence: fact.confidence,
            sourceSessionId: fact.sourceSessionId,
            sourceTurnId: fact.sourceTurnId,
            expiresAt,
          },
        });
        await transaction.memoryWriteReceipt.create({
          data: {
            tenantId: scope.tenantId,
            agentId,
            deviceId,
            idempotencyKey: fact.idempotencyKey,
            kind: MemoryWriteKind.FACT,
            expiresAt: new Date(
              now.getTime() + effective.policy.factRetentionDays * DAY_MS,
            ),
          },
        });
        if (!current) factCount += 1;
        accepted += 1;
      }
      if (accepted > 0 || expired.count > 0 || expiredReceipts.count > 0) {
        await this.audit.record(
          {
            tenantId: scope.tenantId,
            action: "memory.facts.upsert",
            targetType: "device_memory",
            targetId: deviceId,
            requestId: `memory-facts:${facts[0]?.idempotencyKey ?? now.toISOString()}`,
            details: {
              agentId,
              configVersion,
              accepted,
              duplicates,
              rejected,
              expiredDeleted: expired.count,
              expiredReceiptsDeleted: expiredReceipts.count,
            },
          },
          transaction,
        );
      }
      return { accepted, duplicates, rejected };
    });
  }

  async listMessages(
    tenantId: string,
    agentId: string,
    deviceId: string,
    limit: number,
    cursor?: string,
    access?: MemoryMutationContext,
  ): Promise<MemoryPage<MemoryMessageRecord>> {
    if (access) {
      if (access.principal.tenantId !== tenantId) {
        throw new NotFoundException("Memory scope not found");
      }
    }
    return this.prisma.$transaction(async (transaction) => {
      const policy = await this.currentPublicPolicy(
        tenantId,
        agentId,
        deviceId,
        transaction,
      );
      await reconcileMemoryPolicy(
        transaction,
        { tenantId, agentId, deviceId },
        policy,
      );
      const decoded = cursor ? this.decodeCursor(cursor) : undefined;
      const rows = await transaction.conversationMemoryMessage.findMany({
        where: {
          tenantId,
          agentId,
          deviceId,
          retentionUntil: { gt: new Date() },
          redacted: false,
          ...(decoded
            ? {
                OR: [
                  { occurredAt: { lt: new Date(decoded.at) } },
                  { occurredAt: new Date(decoded.at), id: { lt: decoded.id } },
                ],
              }
            : {}),
        },
        orderBy: [{ occurredAt: "desc" }, { id: "desc" }],
        take: Math.min(limit, policy.maxMessages) + 1,
      });
      const effectiveLimit = Math.min(limit, policy.maxMessages);
      const hasMore = rows.length > effectiveLimit;
      const page = rows.slice(0, effectiveLimit);
      const result = {
        items: page.map((row) => this.messageRecord(row)),
        ...(hasMore && page.length
          ? {
              nextCursor: this.encodeCursor({
                at: page[page.length - 1]!.occurredAt.toISOString(),
                id: page[page.length - 1]!.id,
              }),
            }
          : {}),
      };
      if (access) {
        await this.audit.record(
          {
            tenantId,
            actorUserId: access.principal.userId,
            action: "memory.messages.view",
            targetType: "device_memory",
            targetId: deviceId,
            requestId: access.requestId,
            details: { agentId, returned: result.items.length, paginated: Boolean(cursor) },
          },
          transaction,
        );
      }
      return result;
    });
  }

  async exportMemory(
    agentId: string,
    deviceId: string,
    context: MemoryMutationContext,
  ): Promise<MemoryExportRecord> {
    const now = new Date();
    return this.prisma.$transaction(async (transaction) => {
      const policy = await this.currentPublicPolicy(
        context.principal.tenantId,
        agentId,
        deviceId,
        transaction,
      );
      await reconcileMemoryPolicy(
        transaction,
        { tenantId: context.principal.tenantId, agentId, deviceId },
        policy,
      );
      const [rawMessages, rawFacts] = await Promise.all([
        transaction.conversationMemoryMessage.findMany({
          where: {
            tenantId: context.principal.tenantId,
            agentId,
            deviceId,
            retentionUntil: { gt: now },
            redacted: false,
          },
          orderBy: [{ occurredAt: "desc" }, { id: "desc" }],
          take: Math.min(MEMORY_EXPORT_MAX_MESSAGES, policy.maxMessages),
        }),
        transaction.conversationMemoryFact.findMany({
          where: {
            tenantId: context.principal.tenantId,
            agentId,
            deviceId,
            expiresAt: { gt: now },
          },
          orderBy: [{ updatedAt: "desc" }, { id: "desc" }],
          take: Math.min(MEMORY_EXPORT_MAX_FACTS, policy.maxFacts),
        }),
      ]);
      const messages = rawMessages
        .map((message) => this.messageRecord(
          message,
          message.content.slice(0, MEMORY_EXPORT_MAX_MESSAGE_CHARACTERS),
        ))
        .reverse();
      const facts = rawFacts.map((fact) => this.factRecord(
        fact,
        fact.value.slice(0, MEMORY_EXPORT_MAX_FACT_CHARACTERS),
      ));
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "memory.export.create",
          targetType: "device_memory",
          targetId: deviceId,
          requestId: context.requestId,
          details: {
            agentId,
            messageCount: messages.length,
            factCount: facts.length,
            messageCharacters: messages.reduce((total, item) => total + item.content.length, 0),
            factCharacters: facts.reduce((total, item) => total + item.value.length, 0),
          },
        },
        transaction,
      );
      return {
        version: 1,
        exportedAt: now.toISOString(),
        agentId,
        deviceId,
        messages,
        facts,
      };
    });
  }

  async purgeMessages(
    agentId: string,
    deviceId: string,
    context: MemoryMutationContext,
  ): Promise<{ deleted: number }> {
    await this.publicScope(context.principal.tenantId, agentId, deviceId);
    return this.prisma.$transaction(async (transaction) => {
      const result = await transaction.conversationMemoryMessage.deleteMany({
        where: {
          tenantId: context.principal.tenantId,
          agentId,
          deviceId,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "memory.messages.purge",
          targetType: "device_memory",
          targetId: deviceId,
          requestId: context.requestId,
          details: { agentId, deleted: result.count },
        },
        transaction,
      );
      return { deleted: result.count };
    });
  }

  async deleteMessage(
    agentId: string,
    messageId: string,
    context: MemoryMutationContext,
  ): Promise<{ deleted: 1 }> {
    return this.prisma.$transaction(async (transaction) => {
      const message = await transaction.conversationMemoryMessage.findFirst({
        where: {
          id: messageId,
          agentId,
          tenantId: context.principal.tenantId,
        },
        select: { id: true, deviceId: true, sessionId: true, turnId: true, role: true },
      });
      if (!message) throw new NotFoundException("Memory message not found");
      await transaction.conversationMemoryMessage.delete({ where: { id: messageId } });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "memory.message.delete",
          targetType: "memory_message",
          targetId: messageId,
          requestId: context.requestId,
          before: {
            id: message.id,
            deviceId: message.deviceId,
            sessionId: message.sessionId,
            turnId: message.turnId,
            role: message.role,
          },
        },
        transaction,
      );
      return { deleted: 1 };
    });
  }

  async listFacts(
    tenantId: string,
    agentId: string,
    deviceId: string,
    limit: number,
    cursor?: string,
    access?: MemoryMutationContext,
  ): Promise<MemoryPage<MemoryFactRecord>> {
    if (access) {
      if (access.principal.tenantId !== tenantId) {
        throw new NotFoundException("Memory scope not found");
      }
    }
    return this.prisma.$transaction(async (transaction) => {
      const policy = await this.currentPublicPolicy(
        tenantId,
        agentId,
        deviceId,
        transaction,
      );
      await reconcileMemoryPolicy(
        transaction,
        { tenantId, agentId, deviceId },
        policy,
      );
      const decoded = cursor ? this.decodeCursor(cursor) : undefined;
      const rows = await transaction.conversationMemoryFact.findMany({
        where: {
          tenantId,
          agentId,
          deviceId,
          expiresAt: { gt: new Date() },
          ...(decoded
            ? {
                OR: [
                  { updatedAt: { lt: new Date(decoded.at) } },
                  { updatedAt: new Date(decoded.at), id: { lt: decoded.id } },
                ],
              }
            : {}),
        },
        orderBy: [{ updatedAt: "desc" }, { id: "desc" }],
        take: Math.min(limit, policy.maxFacts) + 1,
      });
      const effectiveLimit = Math.min(limit, policy.maxFacts);
      const hasMore = rows.length > effectiveLimit;
      const page = rows.slice(0, effectiveLimit);
      const result = {
        items: page.map((row) => this.factRecord(row)),
        ...(hasMore && page.length
          ? {
              nextCursor: this.encodeCursor({
                at: page[page.length - 1]!.updatedAt.toISOString(),
                id: page[page.length - 1]!.id,
              }),
            }
          : {}),
      };
      if (access) {
        await this.audit.record(
          {
            tenantId,
            actorUserId: access.principal.userId,
            action: "memory.facts.view",
            targetType: "device_memory",
            targetId: deviceId,
            requestId: access.requestId,
            details: { agentId, returned: result.items.length, paginated: Boolean(cursor) },
          },
          transaction,
        );
      }
      return result;
    });
  }

  async updateFact(
    agentId: string,
    factId: string,
    input: { value?: string; confidence?: number; expiresAt?: string },
    context: MemoryMutationContext,
  ): Promise<MemoryFactRecord> {
    if (input.value === undefined && input.confidence === undefined && input.expiresAt === undefined) {
      throw new BadRequestException("At least one memory fact field is required");
    }
    return this.prisma.$transaction(async (transaction) => {
      const fact = await transaction.conversationMemoryFact.findFirst({
        where: { id: factId, agentId, tenantId: context.principal.tenantId },
      });
      if (!fact) throw new NotFoundException("Memory fact not found");
      const policy = await this.latestPolicy(agentId, context.principal.tenantId, transaction);
      if (input.value !== undefined && (
        !input.value.trim() || input.value.length > policy.maxFactCharacters
      )) {
        throw new BadRequestException(
          `Memory fact value must contain 1 to ${policy.maxFactCharacters} characters`,
        );
      }
      if (input.value !== undefined) this.rejectUnsafeText(input.value, "Memory fact value");
      if (
        input.confidence !== undefined &&
        (!Number.isFinite(input.confidence) || input.confidence < 0 || input.confidence > 1)
      ) {
        throw new BadRequestException("Memory fact confidence must be between 0 and 1");
      }
      let expiresAt: Date | undefined;
      if (input.expiresAt !== undefined) {
        expiresAt = new Date(input.expiresAt);
        const maximum = Date.now() + policy.factRetentionDays * DAY_MS;
        if (
          !Number.isFinite(expiresAt.getTime()) ||
          expiresAt.getTime() <= Date.now() ||
          expiresAt.getTime() > maximum
        ) {
          throw new BadRequestException("Memory fact expiry is outside the current policy");
        }
      }
      const updated = await transaction.conversationMemoryFact.update({
        where: { id: factId },
        data: {
          ...(input.value !== undefined ? { value: input.value.trim() } : {}),
          ...(input.confidence !== undefined ? { confidence: input.confidence } : {}),
          ...(expiresAt ? { expiresAt } : {}),
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "memory.fact.update",
          targetType: "memory_fact",
          targetId: factId,
          requestId: context.requestId,
          before: {
            confidence: fact.confidence,
            expiresAt: fact.expiresAt.toISOString(),
            valueCharacters: fact.value.length,
          },
          after: {
            confidence: updated.confidence,
            expiresAt: updated.expiresAt.toISOString(),
            valueCharacters: updated.value.length,
          },
        },
        transaction,
      );
      return this.factRecord(updated);
    });
  }

  async deleteFact(
    agentId: string,
    factId: string,
    context: MemoryMutationContext,
  ): Promise<{ deleted: 1 }> {
    return this.prisma.$transaction(async (transaction) => {
      const fact = await transaction.conversationMemoryFact.findFirst({
        where: { id: factId, agentId, tenantId: context.principal.tenantId },
        select: { id: true, deviceId: true, category: true, key: true },
      });
      if (!fact) throw new NotFoundException("Memory fact not found");
      await transaction.conversationMemoryFact.delete({ where: { id: factId } });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "memory.fact.delete",
          targetType: "memory_fact",
          targetId: factId,
          requestId: context.requestId,
          before: fact,
        },
        transaction,
      );
      return { deleted: 1 };
    });
  }

  private async internalScope(
    agentId: string,
    deviceId: string,
    configVersion: number,
  ): Promise<InternalScope> {
    const config = await this.prisma.agentConfigVersion.findUnique({
      where: { agentId_version: { agentId, version: configVersion } },
      include: { agent: { select: { tenantId: true } } },
    });
    if (!config) throw new NotFoundException("Agent config version not found");
    const device = await this.prisma.device.findFirst({
      where: { id: deviceId, tenantId: config.agent.tenantId, agentId },
      select: { id: true },
    });
    if (!device) throw new NotFoundException("Device and agent assignment not found");
    const snapshot = config.snapshot as Record<string, unknown>;
    return {
      tenantId: config.agent.tenantId,
      agentId,
      deviceId,
      configVersion,
      policy: normalizeMemoryPolicy(snapshot.memoryPolicy),
    };
  }

  private async effectivePublishedScope(
    requested: InternalScope,
    database: Prisma.TransactionClient,
  ): Promise<InternalScope> {
    await database.$queryRaw(
      Prisma.sql`
        SELECT "id" FROM "Agent"
        WHERE "id" = ${requested.agentId} AND "tenantId" = ${requested.tenantId}
        FOR SHARE
      `,
    );
    const agent = await database.agent.findFirst({
      where: { id: requested.agentId, tenantId: requested.tenantId },
      select: { publishedVersion: true },
    });
    if (!agent) throw new NotFoundException("Agent not found");
    const lockedDevices = await database.$queryRaw<Array<{ id: string }>>(
      Prisma.sql`
        SELECT "id" FROM "Device"
        WHERE "id" = ${requested.deviceId}
          AND "tenantId" = ${requested.tenantId}
          AND "agentId" = ${requested.agentId}
        FOR UPDATE
      `,
    );
    if (lockedDevices.length !== 1) {
      throw new NotFoundException("Device and agent assignment not found");
    }
    let publishedPolicy = normalizeMemoryPolicy(undefined);
    if (agent.publishedVersion === requested.configVersion) {
      publishedPolicy = requested.policy;
    } else if (agent.publishedVersion > 0) {
      const current = await database.agentConfigVersion.findUnique({
        where: {
          agentId_version: {
            agentId: requested.agentId,
            version: agent.publishedVersion,
          },
        },
        select: { snapshot: true },
      });
      if (!current) {
        throw new ConflictException("Current published memory policy is unavailable");
      }
      publishedPolicy = normalizeMemoryPolicy(
        (current.snapshot as Record<string, unknown>).memoryPolicy,
      );
    }
    return {
      ...requested,
      policy: this.clampMemoryPolicy(requested.policy, publishedPolicy),
    };
  }

  private clampMemoryPolicy(requested: MemoryPolicy, published: MemoryPolicy): MemoryPolicy {
    return {
      enabled: requested.enabled && published.enabled,
      consent: requested.consent && published.consent,
      storeMessages: requested.storeMessages && published.storeMessages,
      storeFacts: requested.storeFacts && published.storeFacts,
      retentionDays: Math.min(requested.retentionDays, published.retentionDays),
      maxMessages: Math.min(requested.maxMessages, published.maxMessages),
      maxMessageCharacters: Math.min(
        requested.maxMessageCharacters,
        published.maxMessageCharacters,
      ),
      maxContextCharacters: Math.min(
        requested.maxContextCharacters,
        published.maxContextCharacters,
      ),
      factRetentionDays: Math.min(requested.factRetentionDays, published.factRetentionDays),
      maxFacts: Math.min(requested.maxFacts, published.maxFacts),
      maxFactCharacters: Math.min(
        requested.maxFactCharacters,
        published.maxFactCharacters,
      ),
    };
  }

  private async publicScope(tenantId: string, agentId: string, deviceId: string): Promise<void> {
    const [agent, device] = await Promise.all([
      this.prisma.agent.findFirst({ where: { id: agentId, tenantId }, select: { id: true } }),
      this.prisma.device.findFirst({
        where: { id: deviceId, tenantId },
        select: { id: true },
      }),
    ]);
    if (!agent) throw new NotFoundException("Agent not found");
    if (!device) throw new NotFoundException("Device not found");
  }

  private async currentPublicPolicy(
    tenantId: string,
    agentId: string,
    deviceId: string,
    database: Prisma.TransactionClient,
  ): Promise<MemoryPolicy> {
    await database.$queryRaw(
      Prisma.sql`
        SELECT "id" FROM "Agent"
        WHERE "id" = ${agentId} AND "tenantId" = ${tenantId}
        FOR SHARE
      `,
    );
    const agent = await database.agent.findFirst({
      where: { id: agentId, tenantId },
      select: { publishedVersion: true, draftConfig: true },
    });
    if (!agent) throw new NotFoundException("Agent not found");
    const devices = await database.$queryRaw<Array<{ id: string }>>(
      Prisma.sql`
        SELECT "id" FROM "Device"
        WHERE "id" = ${deviceId} AND "tenantId" = ${tenantId}
        FOR UPDATE
      `,
    );
    if (devices.length !== 1) throw new NotFoundException("Device not found");
    if (agent.publishedVersion <= 0) {
      return normalizeMemoryPolicy(
        (agent.draftConfig as Record<string, unknown>).memoryPolicy,
      );
    }
    const config = await database.agentConfigVersion.findUnique({
      where: { agentId_version: { agentId, version: agent.publishedVersion } },
      select: { snapshot: true },
    });
    if (!config) throw new ConflictException("Current published memory policy is unavailable");
    return normalizeMemoryPolicy(
      (config.snapshot as Record<string, unknown>).memoryPolicy,
    );
  }

  private async latestPolicy(
    agentId: string,
    tenantId: string,
    database: Prisma.TransactionClient,
  ): Promise<MemoryPolicy> {
    const agent = await database.agent.findFirst({
      where: { id: agentId, tenantId },
      select: { publishedVersion: true, draftConfig: true },
    });
    if (!agent) throw new NotFoundException("Agent not found");
    if (agent.publishedVersion > 0) {
      const config = await database.agentConfigVersion.findUnique({
        where: { agentId_version: { agentId, version: agent.publishedVersion } },
        select: { snapshot: true },
      });
      if (config) {
        return normalizeMemoryPolicy((config.snapshot as Record<string, unknown>).memoryPolicy);
      }
    }
    return normalizeMemoryPolicy((agent.draftConfig as Record<string, unknown>).memoryPolicy);
  }

  private requireMemoryStore(policy: MemoryPolicy, store: "messages" | "facts"): void {
    const enabled = store === "messages" ? policy.storeMessages : policy.storeFacts;
    if (!policy.enabled || !policy.consent || !enabled) {
      throw new ConflictException(`Cross-session memory ${store} storage is disabled`);
    }
  }

  private async cleanupExpired(scope: InternalScope): Promise<void> {
    const now = new Date();
    await this.prisma.$transaction(async (transaction) => {
      const [messages, facts, receipts] = await Promise.all([
        transaction.conversationMemoryMessage.deleteMany({
          where: {
            tenantId: scope.tenantId,
            agentId: scope.agentId,
            deviceId: scope.deviceId,
            retentionUntil: { lte: now },
          },
        }),
        transaction.conversationMemoryFact.deleteMany({
          where: {
            tenantId: scope.tenantId,
            agentId: scope.agentId,
            deviceId: scope.deviceId,
            expiresAt: { lte: now },
          },
        }),
        transaction.memoryWriteReceipt.deleteMany({
          where: {
            tenantId: scope.tenantId,
            agentId: scope.agentId,
            deviceId: scope.deviceId,
            expiresAt: { lte: now },
          },
        }),
      ]);
      if (messages.count || facts.count || receipts.count) {
        await this.audit.record(
          {
            tenantId: scope.tenantId,
            action: "memory.retention.cleanup",
            targetType: "device_memory",
            targetId: scope.deviceId,
            requestId: `memory-cleanup:${scope.agentId}:${scope.deviceId}:${now.getTime()}`,
            details: {
              messagesDeleted: messages.count,
              factsDeleted: facts.count,
              receiptsDeleted: receipts.count,
            },
          },
          transaction,
        );
      }
    });
  }

  private async cleanupExpiredGlobal(): Promise<void> {
    const now = new Date();
    await this.prisma.$transaction(async (transaction) => {
      const messages = await transaction.$queryRaw<Array<{ tenantId: string }>>(
        Prisma.sql`
          DELETE FROM "ConversationMemoryMessage"
          WHERE "id" IN (
            SELECT "id" FROM "ConversationMemoryMessage"
            WHERE "retentionUntil" <= ${now}
            ORDER BY "retentionUntil" ASC, "id" ASC
            LIMIT ${RETENTION_CLEANUP_BATCH}
          )
          RETURNING "tenantId"
        `,
      );
      const facts = await transaction.$queryRaw<Array<{ tenantId: string }>>(
        Prisma.sql`
          DELETE FROM "ConversationMemoryFact"
          WHERE "id" IN (
            SELECT "id" FROM "ConversationMemoryFact"
            WHERE "expiresAt" <= ${now}
            ORDER BY "expiresAt" ASC, "id" ASC
            LIMIT ${RETENTION_CLEANUP_BATCH}
          )
          RETURNING "tenantId"
        `,
      );
      const receipts = await transaction.$queryRaw<Array<{ tenantId: string }>>(
        Prisma.sql`
          DELETE FROM "MemoryWriteReceipt"
          WHERE "id" IN (
            SELECT "id" FROM "MemoryWriteReceipt"
            WHERE "expiresAt" <= ${now}
            ORDER BY "expiresAt" ASC, "id" ASC
            LIMIT ${RETENTION_CLEANUP_BATCH}
          )
          RETURNING "tenantId"
        `,
      );
      const counts = new Map<
        string,
        { messagesDeleted: number; factsDeleted: number; receiptsDeleted: number }
      >();
      const increment = (
        tenantId: string,
        field: "messagesDeleted" | "factsDeleted" | "receiptsDeleted",
      ) => {
        const current = counts.get(tenantId) ?? {
          messagesDeleted: 0,
          factsDeleted: 0,
          receiptsDeleted: 0,
        };
        current[field] += 1;
        counts.set(tenantId, current);
      };
      messages.forEach(({ tenantId }) => increment(tenantId, "messagesDeleted"));
      facts.forEach(({ tenantId }) => increment(tenantId, "factsDeleted"));
      receipts.forEach(({ tenantId }) => increment(tenantId, "receiptsDeleted"));
      for (const [tenantId, details] of counts) {
        await this.audit.record(
          {
            tenantId,
            action: "memory.retention.cleanup_global",
            targetType: "memory_retention",
            targetId: tenantId,
            requestId: `memory-cleanup-global:${tenantId}:${now.getTime()}`,
            details,
          },
          transaction,
        );
      }
    });
  }

  private async auditMemoryContextLoad(
    scope: InternalScope,
    messageCount: number,
    factCount: number,
    database: Prisma.TransactionClient,
  ): Promise<void> {
    await this.audit.record(
      {
        tenantId: scope.tenantId,
        action: "memory.context.load",
        targetType: "device_memory",
        targetId: scope.deviceId,
        requestId: `memory-context:${scope.agentId}:${scope.deviceId}:${Date.now()}`,
        details: {
          agentId: scope.agentId,
          configVersion: scope.configVersion,
          enabled: scope.policy.enabled && scope.policy.consent,
          messageCount,
          factCount,
        },
      },
      database,
    );
  }

  private messageRecord(
    message: {
      id: string;
      sessionId: string;
      turnId: string;
      role: MemoryMessageRole;
      content: string;
      occurredAt: Date;
      retentionUntil: Date;
    },
    content = message.content,
  ): MemoryMessageRecord {
    return {
      id: message.id,
      sessionId: message.sessionId,
      turnId: message.turnId,
      role: message.role === MemoryMessageRole.USER ? "user" : "assistant",
      content,
      occurredAt: message.occurredAt.toISOString(),
      retentionUntil: message.retentionUntil.toISOString(),
    };
  }

  private factRecord(
    fact: {
      id: string;
      category: string;
      key: string;
      value: string;
      confidence: number;
      sourceSessionId: string;
      sourceTurnId: string;
      expiresAt: Date;
      updatedAt: Date;
    },
    value = fact.value,
  ): MemoryFactRecord {
    return {
      id: fact.id,
      category: fact.category,
      key: fact.key,
      value,
      confidence: fact.confidence,
      sourceSessionId: fact.sourceSessionId,
      sourceTurnId: fact.sourceTurnId,
      expiresAt: fact.expiresAt.toISOString(),
      updatedAt: fact.updatedAt.toISOString(),
    };
  }

  private encodeCursor(value: CursorValue): string {
    return Buffer.from(JSON.stringify(value), "utf8").toString("base64url");
  }

  private decodeCursor(value: string): CursorValue {
    try {
      const parsed = JSON.parse(Buffer.from(value, "base64url").toString("utf8")) as unknown;
      if (
        !parsed ||
        typeof parsed !== "object" ||
        typeof (parsed as CursorValue).at !== "string" ||
        !Number.isFinite(new Date((parsed as CursorValue).at).getTime()) ||
        typeof (parsed as CursorValue).id !== "string" ||
        !(parsed as CursorValue).id
      ) {
        throw new Error("invalid cursor");
      }
      return parsed as CursorValue;
    } catch {
      throw new BadRequestException("Memory pagination cursor is invalid");
    }
  }

  private rejectUnsafeText(value: string, label: string): void {
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value)) {
      throw new BadRequestException(`${label} contains unsupported control characters`);
    }
  }
}
