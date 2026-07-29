import { Prisma } from "@prisma/client";

import type { MemoryPolicy } from "../config/agent-config.policy.js";

interface MemoryRetentionScope {
  tenantId: string;
  agentId: string;
  deviceId?: string;
}

/**
 * Apply the current published storage bounds to existing rows.
 *
 * Receipts deliberately keep their original expiry so a delayed retry cannot recreate
 * content removed by a retention reduction, trim or explicit user deletion.
 */
export async function reconcileMemoryPolicy(
  database: Prisma.TransactionClient,
  scope: MemoryRetentionScope,
  policy: MemoryPolicy,
): Promise<void> {
  const messageScope = scope.deviceId
    ? Prisma.sql`
        "tenantId" = ${scope.tenantId}
        AND "agentId" = ${scope.agentId}
        AND "deviceId" = ${scope.deviceId}
      `
    : Prisma.sql`
        "tenantId" = ${scope.tenantId}
        AND "agentId" = ${scope.agentId}
      `;
  const factScope = messageScope;

  await database.$executeRaw(
    Prisma.sql`
      UPDATE "ConversationMemoryMessage"
      SET
        "occurredAt" = LEAST("occurredAt", "createdAt"),
        "retentionUntil" = LEAST(
          "retentionUntil",
          LEAST("occurredAt", "createdAt")
            + ${policy.retentionDays} * INTERVAL '1 day'
        )
      WHERE ${messageScope}
        AND (
          "occurredAt" > "createdAt"
          OR "retentionUntil" >
            LEAST("occurredAt", "createdAt")
              + ${policy.retentionDays} * INTERVAL '1 day'
        )
    `,
  );
  await database.$executeRaw(
    Prisma.sql`
      DELETE FROM "ConversationMemoryMessage"
      WHERE ${messageScope}
        AND "retentionUntil" <= CURRENT_TIMESTAMP
    `,
  );
  await database.$executeRaw(
    Prisma.sql`
      DELETE FROM "ConversationMemoryMessage"
      WHERE "id" IN (
        SELECT "id"
        FROM (
          SELECT
            "id",
            ROW_NUMBER() OVER (
              PARTITION BY "deviceId"
              ORDER BY "occurredAt" DESC, "id" DESC
            ) AS "position"
          FROM "ConversationMemoryMessage"
          WHERE ${messageScope}
            AND "retentionUntil" > CURRENT_TIMESTAMP
        ) AS "ranked"
        WHERE "position" > ${policy.maxMessages}
      )
    `,
  );

  // Raw SQL intentionally preserves updatedAt while shortening expiresAt. Updating via
  // Prisma would refresh @updatedAt and could accidentally extend the effective lifetime.
  await database.$executeRaw(
    Prisma.sql`
      UPDATE "ConversationMemoryFact"
      SET "expiresAt" = LEAST(
        "expiresAt",
        "updatedAt" + ${policy.factRetentionDays} * INTERVAL '1 day'
      )
      WHERE ${factScope}
        AND "expiresAt" >
          "updatedAt" + ${policy.factRetentionDays} * INTERVAL '1 day'
    `,
  );
  await database.$executeRaw(
    Prisma.sql`
      DELETE FROM "ConversationMemoryFact"
      WHERE ${factScope}
        AND "expiresAt" <= CURRENT_TIMESTAMP
    `,
  );
  await database.$executeRaw(
    Prisma.sql`
      DELETE FROM "ConversationMemoryFact"
      WHERE "id" IN (
        SELECT "id"
        FROM (
          SELECT
            "id",
            ROW_NUMBER() OVER (
              PARTITION BY "deviceId"
              ORDER BY "updatedAt" DESC, "id" DESC
            ) AS "position"
          FROM "ConversationMemoryFact"
          WHERE ${factScope}
            AND "expiresAt" > CURRENT_TIMESTAMP
        ) AS "ranked"
        WHERE "position" > ${policy.maxFacts}
      )
    `,
  );
}
