import { Prisma } from "@prisma/client";

export interface DeviceDesiredStateMutation {
  previousVersion: number;
  previousState: Record<string, unknown>;
  version: number;
  state: Record<string, unknown>;
}

type DesiredStateMutator = (
  current: Record<string, unknown>,
) => Record<string, unknown>;

function record(value: Prisma.JsonValue | undefined): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/**
 * Serializes every desired-state writer on the parent Device row. The parent
 * exists before its optional one-to-one desired-state row, so it is the one
 * stable lock shared by create and update paths.
 */
export async function mutateDeviceDesiredState(
  transaction: Prisma.TransactionClient,
  deviceId: string,
  tenantId: string | undefined,
  mutate: DesiredStateMutator,
): Promise<DeviceDesiredStateMutation | null> {
  const locked = tenantId === undefined
    ? await transaction.$queryRaw<Array<{ id: string }>>(
        Prisma.sql`
          SELECT "id" FROM "Device"
          WHERE "id" = ${deviceId}
          FOR UPDATE
        `,
      )
    : await transaction.$queryRaw<Array<{ id: string }>>(
        Prisma.sql`
          SELECT "id" FROM "Device"
          WHERE "id" = ${deviceId} AND "tenantId" = ${tenantId}
          FOR UPDATE
        `,
      );
  if (locked.length !== 1) return null;

  const current = await transaction.deviceDesiredState.findUnique({
    where: { deviceId },
    select: { version: true, state: true },
  });
  const previousState = record(current?.state);
  const state = mutate({ ...previousState });
  const desired = await transaction.deviceDesiredState.upsert({
    where: { deviceId },
    create: {
      deviceId,
      version: 1,
      state: state as Prisma.InputJsonValue,
    },
    update: {
      version: { increment: 1 },
      state: state as Prisma.InputJsonValue,
    },
    select: { version: true },
  });
  return {
    previousVersion: current?.version ?? 0,
    previousState,
    version: desired.version,
    state,
  };
}

export function mergeDeviceDesiredState(
  transaction: Prisma.TransactionClient,
  deviceId: string,
  tenantId: string | undefined,
  patch: Record<string, unknown>,
): Promise<DeviceDesiredStateMutation | null> {
  return mutateDeviceDesiredState(
    transaction,
    deviceId,
    tenantId,
    (current) => ({ ...current, ...patch }),
  );
}
