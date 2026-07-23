import { createHash } from "node:crypto";

import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  ArtifactKind,
  ArtifactStatus,
  FirmwareRolloutStatus,
  Prisma,
} from "@prisma/client";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";

export interface FirmwareReleaseRecord {
  id: string;
  version: string;
  channel: string;
  sizeBytes: number;
  sha256: string;
  contentType: string;
  board: string;
  signatureKeyId: string;
  securityEpoch: number;
  status: "validated" | "published" | "revoked";
  publishedAt?: string;
  createdAt: string;
}

export interface FirmwareRolloutRecord {
  id: string;
  artifactId: string;
  previousArtifactId?: string;
  channel: string;
  percentage: number;
  canaryDeviceIds: string[];
  status: "draft" | "running" | "paused" | "completed" | "failed" | "rolled_back";
  selectedDeviceIds: string[];
  activeDeviceIds: string[];
  failedDeviceIds: string[];
  createdAt: string;
  updatedAt: string;
}

export interface FirmwareMutationContext {
  principal: Principal;
  requestId: string;
}

@Injectable()
export class FirmwareRolloutService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
  ) {}

  async listReleases(tenantId: string): Promise<FirmwareReleaseRecord[]> {
    const artifacts = await this.prisma.artifact.findMany({
      where: { tenantId, kind: ArtifactKind.FIRMWARE },
      orderBy: { createdAt: "desc" },
    });
    return artifacts.map((artifact) => this.releaseRecord(artifact));
  }

  async publishRelease(id: string, context: FirmwareMutationContext): Promise<FirmwareReleaseRecord> {
    const artifact = await this.prisma.artifact.findFirst({
      where: { id, tenantId: context.principal.tenantId, kind: ArtifactKind.FIRMWARE },
    });
    if (!artifact) throw new NotFoundException("Firmware release not found");
    if (artifact.status === ArtifactStatus.REVOKED) {
      throw new ConflictException("Revoked firmware cannot be published");
    }
    const updated = await this.prisma.artifact.update({
      where: { id },
      data: { status: ArtifactStatus.PUBLISHED, publishedAt: artifact.publishedAt ?? new Date() },
    });
    await this.audit.record({
      tenantId: context.principal.tenantId,
      actorUserId: context.principal.userId,
      action: "firmware.release.publish",
      targetType: "artifact",
      targetId: id,
      requestId: context.requestId,
      before: { status: artifact.status.toLowerCase() },
      after: { status: "published" },
    });
    return this.releaseRecord(updated);
  }

  async listRollouts(tenantId: string): Promise<FirmwareRolloutRecord[]> {
    const rollouts = await this.prisma.firmwareRollout.findMany({
      where: { tenantId },
      include: { artifact: true },
      orderBy: { createdAt: "desc" },
      take: 100,
    });
    return Promise.all(rollouts.map((rollout) => this.rolloutRecord(rollout)));
  }

  async createRollout(
    input: {
      artifactId: string;
      percentage: number;
      canaryDeviceIds: string[];
      channel?: string;
    },
    context: FirmwareMutationContext,
  ): Promise<FirmwareRolloutRecord> {
    if (!Number.isInteger(input.percentage) || input.percentage < 0 || input.percentage > 100) {
      throw new BadRequestException("Rollout percentage must be between 0 and 100");
    }
    const artifact = await this.prisma.artifact.findFirst({
      where: {
        id: input.artifactId,
        tenantId: context.principal.tenantId,
        kind: ArtifactKind.FIRMWARE,
      },
    });
    if (!artifact) throw new NotFoundException("Firmware release not found");
    if (artifact.status !== ArtifactStatus.PUBLISHED) {
      throw new ConflictException("Firmware release must be published before rollout");
    }
    const channel = input.channel ?? artifact.channel;
    if (channel !== artifact.channel) throw new ConflictException("Rollout channel does not match release");
    const canaryIds = [...new Set(input.canaryDeviceIds)];
    if (channel === "stable" && canaryIds.length === 0) {
      throw new BadRequestException("Stable rollout requires at least one canary device");
    }
    const devices = await this.prisma.device.findMany({
      where: { tenantId: context.principal.tenantId, id: { in: canaryIds } },
      select: { id: true },
    });
    if (devices.length !== canaryIds.length) throw new NotFoundException("A canary device was not found");
    const activeRollout = await this.prisma.firmwareRollout.findFirst({
      where: {
        tenantId: context.principal.tenantId,
        status: {
          in: [FirmwareRolloutStatus.RUNNING, FirmwareRolloutStatus.PAUSED],
        },
      },
      select: { id: true },
    });
    if (activeRollout) {
      throw new ConflictException("Pause, complete, fail or roll back the active firmware rollout first");
    }
    const previous = await this.prisma.artifact.findFirst({
      where: {
        tenantId: context.principal.tenantId,
        kind: ArtifactKind.FIRMWARE,
        status: ArtifactStatus.PUBLISHED,
        channel,
        id: { not: artifact.id },
        createdAt: { lt: artifact.createdAt },
      },
      orderBy: { createdAt: "desc" },
    });
    const rollout = await this.prisma.firmwareRollout.create({
      data: {
        tenantId: context.principal.tenantId,
        artifactId: artifact.id,
        ...(previous?.id ? { previousArtifactId: previous.id } : {}),
        channel,
        percentage: input.percentage,
        canaryDeviceIds: canaryIds,
        selectedDeviceIds: canaryIds,
        status: FirmwareRolloutStatus.RUNNING,
      },
    });
    await this.applyDesired(rollout.id, context.principal.tenantId);
    const fresh = await this.prisma.firmwareRollout.findUniqueOrThrow({
      where: { id: rollout.id },
      include: { artifact: true },
    });
    await this.audit.record({
      tenantId: context.principal.tenantId,
      actorUserId: context.principal.userId,
      action: "firmware.rollout.create",
      targetType: "firmware_rollout",
      targetId: rollout.id,
      requestId: context.requestId,
      after: { artifactId: artifact.id, percentage: input.percentage, canaryDeviceIds: canaryIds },
    });
    return this.rolloutRecord(fresh);
  }

  async pause(id: string, context: FirmwareMutationContext): Promise<FirmwareRolloutRecord> {
    return this.transition(id, FirmwareRolloutStatus.PAUSED, context, "firmware.rollout.pause");
  }

  async resume(
    id: string,
    percentage: number | undefined,
    context: FirmwareMutationContext,
  ): Promise<FirmwareRolloutRecord> {
    const rollout = await this.find(id, context.principal.tenantId);
    if (
      rollout.status !== FirmwareRolloutStatus.PAUSED &&
      rollout.status !== FirmwareRolloutStatus.RUNNING
    ) {
      throw new ConflictException("Only a paused or running rollout can resume");
    }
    if (!(await this.canariesActive(rollout.tenantId, rollout.canaryDeviceIds, rollout.artifact.version))) {
      throw new ConflictException("Canary devices must report the target firmware active before percentage rollout resumes");
    }
    const nextPercentage = percentage ?? rollout.percentage;
    if (
      !Number.isInteger(nextPercentage) ||
      nextPercentage < rollout.percentage ||
      nextPercentage > 100
    ) {
      throw new BadRequestException("Resume percentage must not decrease and must be at most 100");
    }
    await this.prisma.firmwareRollout.update({
      where: { id },
      data: { status: FirmwareRolloutStatus.RUNNING, percentage: nextPercentage },
    });
    await this.applyDesired(id, context.principal.tenantId);
    return this.rolloutRecord(await this.find(id, context.principal.tenantId));
  }

  async rollback(id: string, context: FirmwareMutationContext): Promise<FirmwareRolloutRecord> {
    const rollout = await this.find(id, context.principal.tenantId);
    if (!rollout.previousArtifactId) throw new ConflictException("No previous firmware release is available");
    const previous = await this.prisma.artifact.findFirst({
      where: {
        id: rollout.previousArtifactId,
        tenantId: context.principal.tenantId,
        kind: ArtifactKind.FIRMWARE,
        status: ArtifactStatus.PUBLISHED,
        channel: rollout.channel,
      },
    });
    if (!previous) throw new ConflictException("Previous firmware release is no longer published");
    await this.prisma.firmwareRollout.update({
      where: { id },
      data: { status: FirmwareRolloutStatus.ROLLED_BACK },
    });
    const selected = rollout.selectedDeviceIds;
    await this.prisma.$transaction(async (transaction) => {
      for (const deviceId of selected) {
        await this.writeDesired(transaction, deviceId, {
          firmwareVersion: previous.version,
          firmwareManifestId: previous.id,
          firmwareChannel: previous.channel,
        });
      }
    });
    await this.audit.record({
      tenantId: context.principal.tenantId,
      actorUserId: context.principal.userId,
      action: "firmware.rollout.rollback",
      targetType: "firmware_rollout",
      targetId: id,
      requestId: context.requestId,
      after: { artifactId: previous.id, selectedDeviceIds: selected },
    });
    return this.rolloutRecord(await this.find(id, context.principal.tenantId));
  }

  private async transition(
    id: string,
    status: FirmwareRolloutStatus,
    context: FirmwareMutationContext,
    action: string,
  ): Promise<FirmwareRolloutRecord> {
    const rollout = await this.find(id, context.principal.tenantId);
    if (
      rollout.status === FirmwareRolloutStatus.COMPLETED ||
      rollout.status === FirmwareRolloutStatus.FAILED ||
      rollout.status === FirmwareRolloutStatus.ROLLED_BACK
    ) {
      throw new ConflictException("Terminal rollout cannot be changed");
    }
    await this.prisma.firmwareRollout.update({ where: { id }, data: { status } });
    await this.audit.record({
      tenantId: context.principal.tenantId,
      actorUserId: context.principal.userId,
      action,
      targetType: "firmware_rollout",
      targetId: id,
      requestId: context.requestId,
      after: { status: status.toLowerCase() },
    });
    return this.rolloutRecord(await this.find(id, context.principal.tenantId));
  }

  private async applyDesired(id: string, tenantId: string): Promise<void> {
    const rollout = await this.find(id, tenantId);
    const selected = await this.selectedDevices(
      id,
      tenantId,
      rollout.percentage,
      rollout.canaryDeviceIds,
      rollout.artifact.version,
    );
    const assigned = [...new Set([...rollout.selectedDeviceIds, ...selected])];
    const artifact = rollout.artifact;
    await this.prisma.$transaction(async (transaction) => {
      await transaction.firmwareRollout.update({
        where: { id },
        data: { selectedDeviceIds: assigned },
      });
      for (const deviceId of assigned) {
        await this.writeDesired(transaction, deviceId, {
          firmwareVersion: artifact.version,
          firmwareManifestId: artifact.id,
          firmwareChannel: artifact.channel,
        });
      }
    });
  }

  private async selectedDevices(
    rolloutId: string,
    tenantId: string,
    percentage: number,
    canaryIds: string[],
    targetVersion: string,
  ): Promise<string[]> {
    const devices = await this.prisma.device.findMany({
      where: { tenantId },
      include: { reportedState: true },
    });
    const canaries = new Set(canaryIds);
    const canaryDevices = devices.filter((device) => canaries.has(device.id));
    const canaryPassed = canaryIds.length === 0 ||
      (canaryDevices.length === canaryIds.length &&
        canaryDevices.every((device) =>
          firmwareIsActive(device.reportedState?.state, targetVersion)));
    return devices
      .filter((device) =>
        canaries.has(device.id) ||
        (canaryPassed && stableBucket(`${rolloutId}:${device.id}`) < percentage))
      .map((device) => device.id);
  }

  private async canariesActive(
    tenantId: string,
    canaryIds: string[],
    targetVersion: string,
  ): Promise<boolean> {
    if (canaryIds.length === 0) return true;
    const devices = await this.prisma.device.findMany({
      where: { tenantId, id: { in: canaryIds } },
      include: { reportedState: true },
    });
    return devices.length === canaryIds.length &&
      devices.every((device) => firmwareIsActive(device.reportedState?.state, targetVersion));
  }

  private async writeDesired(
    transaction: Prisma.TransactionClient,
    deviceId: string,
    firmware: Record<string, string>,
  ): Promise<void> {
    const current = await transaction.deviceDesiredState.findUnique({ where: { deviceId } });
    const next = { ...((current?.state ?? {}) as Record<string, unknown>), ...firmware };
    await transaction.deviceDesiredState.upsert({
      where: { deviceId },
      create: { deviceId, version: 1, state: next as Prisma.InputJsonValue },
      update: { version: (current?.version ?? 0) + 1, state: next as Prisma.InputJsonValue },
    });
  }

  private async find(id: string, tenantId: string) {
    const rollout = await this.prisma.firmwareRollout.findFirst({
      where: { id, tenantId },
      include: { artifact: true },
    });
    if (!rollout) throw new NotFoundException("Firmware rollout not found");
    return rollout;
  }

  private async rolloutRecord(rollout: {
    id: string;
    artifactId: string;
    previousArtifactId: string | null;
    channel: string;
    percentage: number;
    canaryDeviceIds: string[];
    selectedDeviceIds: string[];
    status: FirmwareRolloutStatus;
    createdAt: Date;
    updatedAt: Date;
    tenantId: string;
    artifact: { version: string };
  }): Promise<FirmwareRolloutRecord> {
    const selected = rollout.selectedDeviceIds;
    const devices = selected.length
      ? await this.prisma.device.findMany({
          where: { id: { in: selected }, tenantId: rollout.tenantId },
          include: { reportedState: true },
        })
      : [];
    const active = devices
      .filter((device) => {
        return firmwareIsActive(device.reportedState?.state, rollout.artifact.version);
      })
      .map((device) => device.id);
    const failed = devices
      .filter((device) => {
        const firmware = jsonObject(jsonObject(device.reportedState?.state)?.firmware_ota);
        return ["failed", "rolled_back"].includes(String(firmware?.phase));
      })
      .map((device) => device.id);
    return {
      id: rollout.id,
      artifactId: rollout.artifactId,
      ...(rollout.previousArtifactId ? { previousArtifactId: rollout.previousArtifactId } : {}),
      channel: rollout.channel,
      percentage: rollout.percentage,
      canaryDeviceIds: rollout.canaryDeviceIds,
      status: rollout.status.toLowerCase() as FirmwareRolloutRecord["status"],
      selectedDeviceIds: selected,
      activeDeviceIds: active,
      failedDeviceIds: failed,
      createdAt: rollout.createdAt.toISOString(),
      updatedAt: rollout.updatedAt.toISOString(),
    };
  }

  private releaseRecord(artifact: {
    id: string;
    version: string;
    channel: string;
    sizeBytes: number;
    sha256: string;
    contentType: string;
    board: string;
    signatureKeyId: string;
    securityEpoch: number;
    status: ArtifactStatus;
    publishedAt: Date | null;
    createdAt: Date;
  }): FirmwareReleaseRecord {
    return {
      id: artifact.id,
      version: artifact.version,
      channel: artifact.channel,
      sizeBytes: artifact.sizeBytes,
      sha256: artifact.sha256,
      contentType: artifact.contentType,
      board: artifact.board,
      signatureKeyId: artifact.signatureKeyId,
      securityEpoch: artifact.securityEpoch,
      status: artifact.status.toLowerCase() as FirmwareReleaseRecord["status"],
      ...(artifact.publishedAt ? { publishedAt: artifact.publishedAt.toISOString() } : {}),
      createdAt: artifact.createdAt.toISOString(),
    };
  }
}

export function stableBucket(value: string): number {
  return createHash("sha256").update(value).digest().readUInt32BE(0) % 100;
}

function jsonObject(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

export function firmwareIsActive(value: unknown, targetVersion: string): boolean {
  const state = jsonObject(value);
  const ota = jsonObject(state?.firmware_ota);
  const firmware = jsonObject(state?.firmware);
  return ota?.phase === "active" &&
    (ota.currentVersion === targetVersion || firmware?.version === targetVersion);
}
