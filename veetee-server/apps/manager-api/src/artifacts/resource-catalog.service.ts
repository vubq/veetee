import {
  ArtifactBenchmarkStatus,
  ArtifactKind,
  ArtifactStatus,
  DeviceStatus,
  Prisma,
  ResourceRolloutStatus,
} from "@prisma/client";
import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";
import { ResourceManifestService } from "./resource-manifest.service.js";

interface MutationContext {
  principal: Principal;
  requestId: string;
}

export interface ArtifactRecord {
  id: string;
  kind: "resource_bundle" | "model_pack" | "display_assets" | "audio_assets" | "admission_model";
  version: string;
  channel: string;
  sizeBytes: number;
  sha256: string;
  contentType: string;
  runtime: string;
  runtimeAbi: number;
  license: string;
  board: string;
  minFirmware: string;
  maxFirmware: string;
  signatureKeyId: string;
  securityEpoch: number;
  benchmarkStatus: "not_run" | "passed" | "failed";
  status: "validated" | "published" | "revoked";
  publishedAt?: string;
  createdAt: string;
}

export interface DetectorProfileInput {
  detectorId: string;
  sensitivity: number;
  cooldownMs: number;
  allowedStates: string[];
}

export interface WakeProfileRecord {
  id: string;
  artifactId: string;
  name: string;
  locale: string;
  channel: string;
  activationPhrase: string;
  activation: DetectorProfileInput;
  interrupt: DetectorProfileInput;
  version: number;
  publishedVersion: number;
  productReady: boolean;
}

export interface ResourceRolloutRecord {
  id: string;
  deviceId: string;
  artifactId: string;
  wakeProfileVersion: number;
  status: "active" | "complete" | "failed" | "rolled_back";
  desiredStateVersion: number;
  createdAt: string;
}

export interface UiPackRolloutRecord {
  id: string;
  deviceId: string;
  artifactId: string;
  status: "active" | "complete" | "failed" | "rolled_back";
  desiredStateVersion: number;
  createdAt: string;
}

export interface WakeProfileInput {
  artifactId: string;
  name: string;
  locale: string;
  channel: string;
  activationPhrase: string;
  activation: DetectorProfileInput;
  interrupt: DetectorProfileInput;
}

const artifactKindToDatabase = {
  resource_bundle: ArtifactKind.RESOURCE_BUNDLE,
  model_pack: ArtifactKind.MODEL_PACK,
  display_assets: ArtifactKind.DISPLAY_ASSETS,
  audio_assets: ArtifactKind.AUDIO_ASSETS,
  admission_model: ArtifactKind.ADMISSION_MODEL,
} as const;

@Injectable()
export class ResourceCatalogService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
    private readonly manifests: ResourceManifestService,
  ) {}

  async listArtifacts(tenantId: string): Promise<ArtifactRecord[]> {
    const artifacts = await this.prisma.artifact.findMany({
      where: { tenantId },
      orderBy: { createdAt: "desc" },
    });
    return artifacts.map((artifact) => this.artifactRecord(artifact));
  }

  async registerArtifact(
    artifactId: string,
    license: string,
    benchmarkStatus: ArtifactRecord["benchmarkStatus"],
    context: MutationContext,
  ): Promise<ArtifactRecord> {
    const validated = await this.manifests.validate(artifactId);
    const existing = await this.prisma.artifact.findUnique({ where: { id: artifactId } });
    if (existing) {
      if (existing.tenantId !== context.principal.tenantId) {
        throw new ConflictException("Artifact id already belongs to another tenant");
      }
      if (existing.sha256 !== validated.sha256) {
        throw new ConflictException("Immutable artifact id has different content");
      }
      return this.artifactRecord(existing);
    }
    return this.prisma.$transaction(async (transaction) => {
      const artifact = await transaction.artifact.create({
        data: {
          id: artifactId,
          tenantId: context.principal.tenantId,
          kind: artifactKindToDatabase[validated.kind],
          version: validated.version,
          channel: validated.channel,
          sizeBytes: validated.sizeBytes,
          sha256: validated.sha256,
          contentType: validated.contentType,
          runtime: validated.runtime,
          runtimeAbi: validated.runtimeAbi,
          license,
          board: validated.board,
          minFirmware: validated.minFirmware,
          maxFirmware: validated.maxFirmware,
          signatureKeyId: validated.signatureKeyId,
          securityEpoch: validated.securityEpoch,
          benchmarkStatus: this.benchmarkToDatabase(benchmarkStatus),
          manifest: validated.manifest as Prisma.InputJsonValue,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "artifact.register",
          targetType: "artifact",
          targetId: artifact.id,
          requestId: context.requestId,
          after: this.artifactRecord(artifact),
        },
        transaction,
      );
      return this.artifactRecord(artifact);
    });
  }

  async publishArtifact(id: string, context: MutationContext): Promise<ArtifactRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const artifact = await transaction.artifact.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!artifact) throw new NotFoundException("Artifact not found");
      if (artifact.status === ArtifactStatus.REVOKED) {
        throw new ConflictException("Revoked artifact cannot be published");
      }
      const updated = await transaction.artifact.update({
        where: { id },
        data: { status: ArtifactStatus.PUBLISHED, publishedAt: artifact.publishedAt ?? new Date() },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "artifact.publish",
          targetType: "artifact",
          targetId: id,
          requestId: context.requestId,
          before: { status: artifact.status.toLowerCase() },
          after: { status: "published" },
        },
        transaction,
      );
      return this.artifactRecord(updated);
    });
  }

  async updateBenchmark(
    id: string,
    benchmarkStatus: ArtifactRecord["benchmarkStatus"],
    context: MutationContext,
  ): Promise<ArtifactRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const artifact = await transaction.artifact.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!artifact) throw new NotFoundException("Artifact not found");
      const updated = await transaction.artifact.update({
        where: { id },
        data: { benchmarkStatus: this.benchmarkToDatabase(benchmarkStatus) },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "artifact.benchmark.update",
          targetType: "artifact",
          targetId: id,
          requestId: context.requestId,
          before: { benchmarkStatus: artifact.benchmarkStatus.toLowerCase() },
          after: { benchmarkStatus },
        },
        transaction,
      );
      return this.artifactRecord(updated);
    });
  }

  async listWakeProfiles(tenantId: string): Promise<WakeProfileRecord[]> {
    const profiles = await this.prisma.wakeProfile.findMany({
      where: { tenantId },
      include: { artifact: true },
      orderBy: { updatedAt: "desc" },
    });
    return profiles.map((profile) => this.wakeProfileRecord(profile));
  }

  async createWakeProfile(
    input: WakeProfileInput,
    context: MutationContext,
  ): Promise<WakeProfileRecord> {
    const artifact = await this.requireArtifact(input.artifactId, context.principal.tenantId);
    return this.prisma.$transaction(async (transaction) => {
      const profile = await transaction.wakeProfile.create({
        data: {
          tenantId: context.principal.tenantId,
          artifactId: input.artifactId,
          name: input.name,
          locale: input.locale,
          channel: input.channel,
          activationPhrase: input.activationPhrase,
          activation: input.activation as unknown as Prisma.InputJsonValue,
          interrupt: input.interrupt as unknown as Prisma.InputJsonValue,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "wake_profile.create",
          targetType: "wake_profile",
          targetId: profile.id,
          requestId: context.requestId,
          after: { ...input, artifactHash: artifact.sha256 },
        },
        transaction,
      );
      return this.wakeProfileRecord({ ...profile, artifact });
    });
  }

  async updateWakeProfile(
    id: string,
    input: WakeProfileInput,
    context: MutationContext,
  ): Promise<WakeProfileRecord> {
    const artifact = await this.requireArtifact(input.artifactId, context.principal.tenantId);
    return this.prisma.$transaction(async (transaction) => {
      const profile = await transaction.wakeProfile.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!profile) throw new NotFoundException("Wake profile not found");
      const updated = await transaction.wakeProfile.update({
        where: { id },
        data: {
          artifactId: input.artifactId,
          name: input.name,
          locale: input.locale,
          channel: input.channel,
          activationPhrase: input.activationPhrase,
          activation: input.activation as unknown as Prisma.InputJsonValue,
          interrupt: input.interrupt as unknown as Prisma.InputJsonValue,
          version: { increment: 1 },
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "wake_profile.update",
          targetType: "wake_profile",
          targetId: id,
          requestId: context.requestId,
          before: this.wakeProfileRecord({ ...profile, artifact }),
          after: this.wakeProfileRecord({ ...updated, artifact }),
        },
        transaction,
      );
      return this.wakeProfileRecord({ ...updated, artifact });
    });
  }

  async publishWakeProfile(id: string, context: MutationContext): Promise<WakeProfileRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const profile = await transaction.wakeProfile.findFirst({
        where: { id, tenantId: context.principal.tenantId },
        include: { artifact: true },
      });
      if (!profile) throw new NotFoundException("Wake profile not found");
      if (profile.artifact.status !== ArtifactStatus.PUBLISHED) {
        throw new ConflictException("Wake profile requires a published signed artifact");
      }
      if (
        profile.channel === "stable" &&
        profile.artifact.benchmarkStatus !== ArtifactBenchmarkStatus.PASSED
      ) {
        throw new ConflictException("Stable wake profile requires a passed wake corpus benchmark");
      }
      const version = profile.version + 1;
      const productReady =
        profile.channel === "stable" &&
        profile.artifact.benchmarkStatus === ArtifactBenchmarkStatus.PASSED;
      const snapshot = this.wakeSnapshot(profile, version, productReady);
      const updated = await transaction.wakeProfile.update({
        where: { id },
        data: { version, publishedVersion: version },
      });
      await transaction.wakeProfileVersion.create({
        data: {
          wakeProfileId: id,
          artifactId: profile.artifactId,
          version,
          snapshot: snapshot as unknown as Prisma.InputJsonValue,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "wake_profile.publish",
          targetType: "wake_profile",
          targetId: id,
          requestId: context.requestId,
          before: { publishedVersion: profile.publishedVersion },
          after: { publishedVersion: version, artifactId: profile.artifactId },
        },
        transaction,
      );
      return this.wakeProfileRecord({ ...updated, artifact: profile.artifact });
    });
  }

  async listRollouts(tenantId: string): Promise<ResourceRolloutRecord[]> {
    const rollouts = await this.prisma.resourceRollout.findMany({
      where: { tenantId },
      include: { wakeProfileVersion: true },
      orderBy: { createdAt: "desc" },
      take: 100,
    });
    return rollouts.map((rollout) => this.rolloutRecord(rollout));
  }

  async listUiPackRollouts(tenantId: string): Promise<UiPackRolloutRecord[]> {
    const rollouts = await this.prisma.uiPackRollout.findMany({
      where: { tenantId },
      orderBy: { createdAt: "desc" },
      take: 100,
    });
    return rollouts.map((rollout) => this.uiPackRolloutRecord(rollout));
  }

  async rolloutUiPack(
    artifactId: string,
    deviceIds: string[],
    context: MutationContext,
  ): Promise<UiPackRolloutRecord[]> {
    const artifact = await this.prisma.artifact.findFirst({
      where: { id: artifactId, tenantId: context.principal.tenantId },
    });
    if (!artifact || artifact.kind !== ArtifactKind.DISPLAY_ASSETS) {
      throw new NotFoundException("UI Pack artifact not found");
    }
    if (artifact.status !== ArtifactStatus.PUBLISHED) {
      throw new ConflictException("UI Pack must be published before rollout");
    }
    const devices = await this.prisma.device.findMany({
      where: { id: { in: deviceIds }, tenantId: context.principal.tenantId },
      include: { desiredState: true, reportedState: true },
    });
    if (devices.length !== new Set(deviceIds).size) {
      throw new NotFoundException("One or more rollout devices were not found");
    }
    for (const device of devices) this.assertUiPackCompatibility(device, artifact);

    return this.prisma.$transaction(async (transaction) => {
      const output: UiPackRolloutRecord[] = [];
      for (const device of devices) {
        const desiredStateVersion = (device.desiredState?.version ?? 0) + 1;
        const current = (device.desiredState?.state ?? {}) as Record<string, unknown>;
        const next = {
          ...current,
          uiPackVersion: artifact.version,
          uiManifestId: artifact.id,
        };
        await transaction.deviceDesiredState.upsert({
          where: { deviceId: device.id },
          create: {
            deviceId: device.id,
            version: desiredStateVersion,
            state: next as Prisma.InputJsonValue,
          },
          update: { version: desiredStateVersion, state: next as Prisma.InputJsonValue },
        });
        const rollout = await transaction.uiPackRollout.create({
          data: {
            tenantId: context.principal.tenantId,
            deviceId: device.id,
            artifactId: artifact.id,
            desiredStateVersion,
          },
        });
        await this.audit.record(
          {
            tenantId: context.principal.tenantId,
            actorUserId: context.principal.userId,
            action: "ui_pack.rollout",
            targetType: "device",
            targetId: device.id,
            requestId: context.requestId,
            before: current,
            after: next,
            details: { rolloutId: rollout.id, artifactId: artifact.id },
          },
          transaction,
        );
        output.push(this.uiPackRolloutRecord(rollout));
      }
      return output;
    });
  }

  async rollout(
    wakeProfileId: string,
    version: number | undefined,
    deviceIds: string[],
    context: MutationContext,
  ): Promise<ResourceRolloutRecord[]> {
    const profile = await this.prisma.wakeProfile.findFirst({
      where: { id: wakeProfileId, tenantId: context.principal.tenantId },
      include: { artifact: true },
    });
    if (!profile) throw new NotFoundException("Wake profile not found");
    const selectedVersion = version ?? profile.publishedVersion;
    if (selectedVersion <= 0) throw new ConflictException("Wake profile is not published");
    const published = await this.prisma.wakeProfileVersion.findUnique({
      where: { wakeProfileId_version: { wakeProfileId, version: selectedVersion } },
    });
    if (!published) throw new NotFoundException("Wake profile version not found");
    if (profile.artifact.status !== ArtifactStatus.PUBLISHED) {
      throw new ConflictException("Artifact is not published");
    }
    const devices = await this.prisma.device.findMany({
      where: { id: { in: deviceIds }, tenantId: context.principal.tenantId },
      include: { desiredState: true, reportedState: true },
    });
    if (devices.length !== new Set(deviceIds).size) {
      throw new NotFoundException("One or more rollout devices were not found");
    }
    for (const device of devices) this.assertDeviceCompatibility(device, profile.artifact);

    return this.prisma.$transaction(async (transaction) => {
      const results: ResourceRolloutRecord[] = [];
      for (const device of devices) {
        const desiredStateVersion = (device.desiredState?.version ?? 0) + 1;
        const current = (device.desiredState?.state ?? {}) as Record<string, unknown>;
        const next = {
          ...current,
          resourceBundleVersion: profile.artifact.version,
          resourceManifestId: profile.artifact.id,
          wakeProfile: published.snapshot,
        };
        await transaction.deviceDesiredState.upsert({
          where: { deviceId: device.id },
          create: {
            deviceId: device.id,
            version: desiredStateVersion,
            state: next as Prisma.InputJsonValue,
          },
          update: { version: desiredStateVersion, state: next as Prisma.InputJsonValue },
        });
        const rollout = await transaction.resourceRollout.create({
          data: {
            tenantId: context.principal.tenantId,
            deviceId: device.id,
            artifactId: profile.artifact.id,
            wakeProfileVersionId: published.id,
            desiredStateVersion,
          },
          include: { wakeProfileVersion: true },
        });
        await this.audit.record(
          {
            tenantId: context.principal.tenantId,
            actorUserId: context.principal.userId,
            action: "resource.rollout",
            targetType: "device",
            targetId: device.id,
            requestId: context.requestId,
            before: current,
            after: next,
            details: { rolloutId: rollout.id },
          },
          transaction,
        );
        results.push(this.rolloutRecord(rollout));
      }
      return results;
    });
  }

  private async requireArtifact(id: string, tenantId: string) {
    const artifact = await this.prisma.artifact.findFirst({ where: { id, tenantId } });
    if (!artifact) throw new NotFoundException("Artifact not found");
    if (artifact.status === ArtifactStatus.REVOKED) {
      throw new ConflictException("Revoked artifact cannot be selected");
    }
    return artifact;
  }

  private assertDeviceCompatibility(
    device: {
      firmwareVersion: string | null;
      status: DeviceStatus;
      reportedState: { state: Prisma.JsonValue } | null;
    },
    artifact: {
      board: string;
      runtime: string;
      runtimeAbi: number;
      sizeBytes: number;
      minFirmware: string;
      maxFirmware: string;
    },
  ): void {
    if (device.status === DeviceStatus.OFFLINE) {
      throw new ConflictException("Device must be connected before resource rollout");
    }
    const capability = reportedCapabilities(device.reportedState?.state).wake;
    if (!capability) {
      throw new ConflictException("Device wake capability has not been reported");
    }
    if (
      capability.board !== artifact.board ||
      capability.runtime !== artifact.runtime ||
      capability.runtimeAbi !== artifact.runtimeAbi ||
      capability.resourceAbi !== 1 ||
      !capability.hotReload ||
      artifact.sizeBytes > capability.slotBytes
    ) {
      throw new ConflictException("Artifact is outside the V1 board capability bounds");
    }
    if (!device.firmwareVersion || !semver.test(device.firmwareVersion)) {
      throw new ConflictException("Device firmware capability is unknown");
    }
    if (
      compareSemver(device.firmwareVersion, artifact.minFirmware) < 0 ||
      compareSemver(device.firmwareVersion, artifact.maxFirmware) >= 0
    ) {
      throw new ConflictException("Device firmware is incompatible with the artifact");
    }
  }

  private assertUiPackCompatibility(
    device: {
      firmwareVersion: string | null;
      status: DeviceStatus;
      reportedState: { state: Prisma.JsonValue } | null;
    },
    artifact: {
      board: string;
      runtime: string;
      runtimeAbi: number;
      sizeBytes: number;
      minFirmware: string;
      maxFirmware: string;
    },
  ): void {
    if (device.status === DeviceStatus.OFFLINE) {
      throw new ConflictException("Device must be connected before UI Pack rollout");
    }
    const capability = reportedCapabilities(device.reportedState?.state).display;
    if (!capability) {
      throw new ConflictException("Device display capability has not been reported");
    }
    if (
      capability.board !== artifact.board ||
      capability.target !== "st7789-240x280-rgb565" ||
      artifact.runtime !== "veetee-ui" ||
      capability.uiAbi !== artifact.runtimeAbi ||
      capability.resourceAbi !== 2 ||
      !capability.hotReload ||
      artifact.sizeBytes > capability.slotBytes
    ) {
      throw new ConflictException("UI Pack is outside the device capability bounds");
    }
    if (!device.firmwareVersion || !semver.test(device.firmwareVersion)) {
      throw new ConflictException("Device firmware capability is unknown");
    }
    if (
      compareSemver(device.firmwareVersion, artifact.minFirmware) < 0 ||
      compareSemver(device.firmwareVersion, artifact.maxFirmware) >= 0
    ) {
      throw new ConflictException("Device firmware is incompatible with the UI Pack");
    }
  }

  private wakeSnapshot(
    profile: {
      id: string;
      artifactId: string;
      name: string;
      locale: string;
      channel: string;
      activationPhrase: string;
      activation: Prisma.JsonValue;
      interrupt: Prisma.JsonValue;
    },
    version: number,
    productReady: boolean,
  ): WakeProfileRecord & { schemaVersion: number } {
    return {
      schemaVersion: 1,
      id: profile.id,
      artifactId: profile.artifactId,
      name: profile.name,
      locale: profile.locale,
      channel: profile.channel,
      activationPhrase: profile.activationPhrase,
      activation: profile.activation as unknown as DetectorProfileInput,
      interrupt: profile.interrupt as unknown as DetectorProfileInput,
      version,
      publishedVersion: version,
      productReady,
    };
  }

  private artifactRecord(artifact: {
    id: string;
    kind: ArtifactKind;
    version: string;
    channel: string;
    sizeBytes: number;
    sha256: string;
    contentType: string;
    runtime: string;
    runtimeAbi: number;
    license: string;
    board: string;
    minFirmware: string;
    maxFirmware: string;
    signatureKeyId: string;
    securityEpoch: number;
    benchmarkStatus: ArtifactBenchmarkStatus;
    status: ArtifactStatus;
    publishedAt: Date | null;
    createdAt: Date;
  }): ArtifactRecord {
    return {
      id: artifact.id,
      kind: artifact.kind.toLowerCase() as ArtifactRecord["kind"],
      version: artifact.version,
      channel: artifact.channel,
      sizeBytes: artifact.sizeBytes,
      sha256: artifact.sha256,
      contentType: artifact.contentType,
      runtime: artifact.runtime,
      runtimeAbi: artifact.runtimeAbi,
      license: artifact.license,
      board: artifact.board,
      minFirmware: artifact.minFirmware,
      maxFirmware: artifact.maxFirmware,
      signatureKeyId: artifact.signatureKeyId,
      securityEpoch: artifact.securityEpoch,
      benchmarkStatus: artifact.benchmarkStatus.toLowerCase() as ArtifactRecord["benchmarkStatus"],
      status: artifact.status.toLowerCase() as ArtifactRecord["status"],
      ...(artifact.publishedAt ? { publishedAt: artifact.publishedAt.toISOString() } : {}),
      createdAt: artifact.createdAt.toISOString(),
    };
  }

  private wakeProfileRecord(profile: {
    id: string;
    artifactId: string;
    name: string;
    locale: string;
    channel: string;
    activationPhrase: string;
    activation: Prisma.JsonValue;
    interrupt: Prisma.JsonValue;
    version: number;
    publishedVersion: number;
    artifact: { benchmarkStatus: ArtifactBenchmarkStatus };
  }): WakeProfileRecord {
    return {
      id: profile.id,
      artifactId: profile.artifactId,
      name: profile.name,
      locale: profile.locale,
      channel: profile.channel,
      activationPhrase: profile.activationPhrase,
      activation: profile.activation as unknown as DetectorProfileInput,
      interrupt: profile.interrupt as unknown as DetectorProfileInput,
      version: profile.version,
      publishedVersion: profile.publishedVersion,
      productReady:
        profile.channel === "stable" &&
        profile.artifact.benchmarkStatus === ArtifactBenchmarkStatus.PASSED,
    };
  }

  private rolloutRecord(rollout: {
    id: string;
    deviceId: string;
    artifactId: string;
    status: ResourceRolloutStatus;
    desiredStateVersion: number;
    createdAt: Date;
    wakeProfileVersion: { version: number };
  }): ResourceRolloutRecord {
    return {
      id: rollout.id,
      deviceId: rollout.deviceId,
      artifactId: rollout.artifactId,
      wakeProfileVersion: rollout.wakeProfileVersion.version,
      status: rollout.status.toLowerCase() as ResourceRolloutRecord["status"],
      desiredStateVersion: rollout.desiredStateVersion,
      createdAt: rollout.createdAt.toISOString(),
    };
  }

  private uiPackRolloutRecord(rollout: {
    id: string;
    deviceId: string;
    artifactId: string;
    status: ResourceRolloutStatus;
    desiredStateVersion: number;
    createdAt: Date;
  }): UiPackRolloutRecord {
    return {
      id: rollout.id,
      deviceId: rollout.deviceId,
      artifactId: rollout.artifactId,
      status: rollout.status.toLowerCase() as UiPackRolloutRecord["status"],
      desiredStateVersion: rollout.desiredStateVersion,
      createdAt: rollout.createdAt.toISOString(),
    };
  }

  private benchmarkToDatabase(value: ArtifactRecord["benchmarkStatus"]): ArtifactBenchmarkStatus {
    if (value === "passed") return ArtifactBenchmarkStatus.PASSED;
    if (value === "failed") return ArtifactBenchmarkStatus.FAILED;
    return ArtifactBenchmarkStatus.NOT_RUN;
  }
}

interface WakeCapability {
  board: string;
  runtime: string;
  runtimeAbi: number;
  resourceAbi: number;
  slotBytes: number;
  hotReload: boolean;
}

interface DisplayCapability {
  board: string;
  target: string;
  uiAbi: number;
  resourceAbi: number;
  slotBytes: number;
  hotReload: boolean;
}

function jsonRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function reportedCapabilities(value: unknown): {
  wake?: WakeCapability;
  display?: DisplayCapability;
} {
  const state = jsonRecord(value);
  const capabilities = jsonRecord(state?.capabilities);
  const board = typeof capabilities?.board === "string" ? capabilities.board : undefined;
  const wake = jsonRecord(capabilities?.wake);
  const display = jsonRecord(capabilities?.display);
  return {
    ...(board && wake && typeof wake.runtime === "string" &&
    Number.isInteger(wake.runtimeAbi) && Number.isInteger(wake.resourceAbi) &&
    Number.isInteger(wake.slotBytes) && typeof wake.hotReload === "boolean"
      ? {
          wake: {
            board,
            runtime: wake.runtime,
            runtimeAbi: wake.runtimeAbi as number,
            resourceAbi: wake.resourceAbi as number,
            slotBytes: wake.slotBytes as number,
            hotReload: wake.hotReload,
          },
        }
      : {}),
    ...(board && display && typeof display.target === "string" &&
    Number.isInteger(display.uiAbi) && Number.isInteger(display.resourceAbi) &&
    Number.isInteger(display.slotBytes) && typeof display.hotReload === "boolean"
      ? {
          display: {
            board,
            target: display.target,
            uiAbi: display.uiAbi as number,
            resourceAbi: display.resourceAbi as number,
            slotBytes: display.slotBytes as number,
            hotReload: display.hotReload,
          },
        }
      : {}),
  };
}

const semver = /^\d+\.\d+\.\d+$/;

function compareSemver(left: string, right: string): number {
  const a = left.split(".").map(Number);
  const b = right.split(".").map(Number);
  for (let index = 0; index < 3; index += 1) {
    const difference = (a[index] ?? 0) - (b[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}
