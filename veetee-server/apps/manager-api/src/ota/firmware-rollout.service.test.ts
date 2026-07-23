import { ConflictException } from "@nestjs/common";
import { ArtifactStatus, FirmwareRolloutStatus } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import {
  FirmwareRolloutService,
  firmwareIsActive,
  stableBucket,
} from "./firmware-rollout.service.js";

const context = {
  principal: {
    userId: "user-1",
    tenantId: "tenant-1",
    tenantSlug: "veetee-local",
    role: "OWNER" as const,
    email: "owner@veetee.local",
    displayName: "Owner",
  },
  requestId: "request-1",
};

describe("firmware rollout policy", () => {
  it("keeps percentage assignment deterministic for a rollout and device", () => {
    expect(stableBucket("rollout-a:device-a")).toBe(stableBucket("rollout-a:device-a"));
    expect(stableBucket("rollout-a:device-a")).toBeGreaterThanOrEqual(0);
    expect(stableBucket("rollout-a:device-a")).toBeLessThan(100);
    expect(new Set(Array.from({ length: 100 }, (_, index) =>
      stableBucket(`rollout-a:device-${index}`))).size).toBeGreaterThan(40);
  });

  it("requires the target version and active OTA acknowledgement", () => {
    expect(firmwareIsActive({
      firmware: { version: "0.4.0" },
      firmware_ota: { phase: "active", currentVersion: "0.4.0" },
    }, "0.4.0")).toBe(true);
    expect(firmwareIsActive({
      firmware: { version: "0.3.0" },
      firmware_ota: { phase: "pending_health", currentVersion: "0.4.0" },
    }, "0.4.0")).toBe(false);
    expect(firmwareIsActive({
      firmware: { version: "0.3.0" },
      firmware_ota: { phase: "active", currentVersion: "0.3.0" },
    }, "0.4.0")).toBe(false);
  });

  it("rejects overlapping active firmware campaigns", async () => {
    const prisma = {
      artifact: {
        findFirst: vi.fn().mockResolvedValue({
          id: "fw-0.4.0",
          channel: "stable",
          status: ArtifactStatus.PUBLISHED,
        }),
      },
      device: {
        findMany: vi.fn().mockResolvedValue([{ id: "device-1" }]),
      },
      firmwareRollout: {
        findFirst: vi.fn().mockResolvedValue({ id: "rollout-active" }),
      },
    };
    const service = new FirmwareRolloutService(
      prisma as never,
      { record: vi.fn() } as never,
    );

    await expect(service.createRollout({
      artifactId: "fw-0.4.0",
      percentage: 10,
      canaryDeviceIds: ["device-1"],
    }, context as never)).rejects.toBeInstanceOf(ConflictException);
  });

  it("rolls back every persisted target even if canary health later changes", async () => {
    const rollout = {
      id: "rollout-1",
      tenantId: "tenant-1",
      artifactId: "fw-0.4.0",
      previousArtifactId: "fw-0.3.0",
      channel: "stable",
      percentage: 50,
      canaryDeviceIds: ["device-canary"],
      selectedDeviceIds: ["device-canary", "device-percentage"],
      status: FirmwareRolloutStatus.FAILED,
      createdAt: new Date("2026-07-23T00:00:00.000Z"),
      updatedAt: new Date("2026-07-23T00:01:00.000Z"),
      artifact: { version: "0.4.0" },
    };
    const upsert = vi.fn().mockResolvedValue(undefined);
    const transaction = {
      deviceDesiredState: {
        findUnique: vi.fn().mockResolvedValue({
          version: 2,
          state: { agentId: "agent-1" },
        }),
        upsert,
      },
    };
    const prisma = {
      firmwareRollout: {
        findFirst: vi.fn().mockResolvedValue(rollout),
        update: vi.fn().mockResolvedValue(undefined),
      },
      artifact: {
        findFirst: vi.fn().mockResolvedValue({
          id: "fw-0.3.0",
          version: "0.3.0",
          channel: "stable",
          status: ArtifactStatus.PUBLISHED,
        }),
      },
      device: {
        findMany: vi.fn().mockResolvedValue([]),
      },
      $transaction: vi.fn(async (callback: (client: typeof transaction) => unknown) =>
        callback(transaction)),
    };
    const service = new FirmwareRolloutService(
      prisma as never,
      { record: vi.fn().mockResolvedValue(undefined) } as never,
    );

    await service.rollback("rollout-1", context as never);

    expect(upsert).toHaveBeenCalledTimes(2);
    expect(upsert.mock.calls.map(([call]) => call.where.deviceId)).toEqual([
      "device-canary",
      "device-percentage",
    ]);
  });
});
