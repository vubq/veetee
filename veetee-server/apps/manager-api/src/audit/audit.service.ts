import { createHash } from "node:crypto";

import { Injectable } from "@nestjs/common";
import { Prisma, type PrismaClient } from "@prisma/client";

import { PrismaService } from "../database/prisma.service.js";

interface AuditInput {
  tenantId: string;
  actorUserId?: string;
  action: string;
  targetType: string;
  targetId: string;
  requestId: string;
  before?: unknown;
  after?: unknown;
  details?: Record<string, unknown>;
}

type AuditDatabase = Pick<PrismaClient, "auditEvent"> | Prisma.TransactionClient;

@Injectable()
export class AuditService {
  constructor(private readonly prisma: PrismaService) {}

  async record(input: AuditInput, database: AuditDatabase = this.prisma): Promise<void> {
    await database.auditEvent.create({
      data: {
        tenantId: input.tenantId,
        actorUserId: input.actorUserId ?? null,
        action: input.action,
        targetType: input.targetType,
        targetId: input.targetId,
        requestId: input.requestId,
        beforeHash: input.before === undefined ? null : this.hash(input.before),
        afterHash: input.after === undefined ? null : this.hash(input.after),
        details: input.details ? (input.details as Prisma.InputJsonValue) : Prisma.JsonNull,
      },
    });
  }

  private hash(value: unknown): string {
    return createHash("sha256").update(this.stableStringify(value)).digest("hex");
  }

  private stableStringify(value: unknown): string {
    if (Array.isArray(value)) return `[${value.map((item) => this.stableStringify(item)).join(",")}]`;
    if (value && typeof value === "object") {
      const object = value as Record<string, unknown>;
      return `{${Object.keys(object)
        .sort()
        .map((key) => `${JSON.stringify(key)}:${this.stableStringify(object[key])}`)
        .join(",")}}`;
    }
    return JSON.stringify(value) ?? "null";
  }
}
