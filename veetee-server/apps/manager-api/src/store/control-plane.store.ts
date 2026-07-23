import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
  UnauthorizedException,
} from "@nestjs/common";
import {
  DeviceStatus,
  InteractionMode,
  FirmwareRolloutStatus,
  Prisma,
  ProviderCircuitState,
  ProviderHealth,
  ProviderKind,
  ResourceRolloutStatus,
} from "@prisma/client";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import {
  expandProviderChains,
  validateAgentDraftConfig,
  type ProviderPolicyBinding,
} from "../config/agent-config.policy.js";
import { normalizePublishedAgentPrompt } from "../config/agent-prompt.policy.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";
import { PairingService } from "../pairing/pairing.service.js";
import {
  probeVoiceRuntimeComponent,
  type VoiceRuntimeComponent,
} from "../providers/voice-runtime-probe.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";

export interface DeviceRecord {
  id: string;
  hardwareId: string;
  name: string;
  status: "online" | "idle" | "offline";
  agentId?: string;
  firmwareVersion?: string;
  desiredState: { version: number; state: Record<string, unknown> };
  reportedState: { version: number; state: Record<string, unknown>; bootId?: string };
  pairedAt: string;
  lastSeenAt?: string;
}

export interface AgentRecord {
  id: string;
  name: string;
  defaultLocale: string;
  interactionMode: "auto" | "manual" | "realtime";
  persona: string;
  draftConfig: Record<string, unknown>;
  version: number;
  publishedVersion: number;
}

export interface ProviderRecord {
  id: string;
  kind: "vad" | "asr" | "llm" | "tts" | "realtime" | "memory";
  adapter: string;
  model: string;
  baseUrl?: string;
  secretConfigured: boolean;
  enabled: boolean;
  priority: number;
  locales: string[];
  health: "unknown" | "healthy" | "degraded";
  healthLatencyMs?: number;
  healthErrorCode?: string;
  healthCheckedAt?: string;
  circuitState: "closed" | "open" | "half_open";
  failureCount: number;
}

export interface ProviderRuntimeRecord {
  id: string;
  kind: ProviderRecord["kind"];
  adapter: string;
  model: string;
  baseUrl?: string;
  secret?: string;
  priority: number;
  locales: string[];
}

export interface ConversationEventInput {
  eventId: string;
  sessionId: string;
  turnId?: string;
  generation: number;
  eventType: string;
  payload: Record<string, unknown>;
  occurredAt: string;
}

export interface ConversationEventRecord {
  id: string;
  deviceId: string;
  agentId?: string;
  sessionId: string;
  turnId?: string;
  generation: number;
  eventType: string;
  payload: Record<string, unknown>;
  occurredAt: string;
}

export interface AuditEventRecord {
  id: string;
  action: string;
  targetType: string;
  targetId: string;
  requestId: string;
  beforeHash?: string;
  afterHash?: string;
  details: Record<string, unknown>;
  actorName?: string;
  createdAt: string;
}

export type DeviceBootstrapResult =
  | {
      state: "unbound";
      activation: { code: string; challenge: string; expiresAt: string };
    }
  | { state: "pending_activation" }
  | {
      state: "active";
      deviceId: string;
      agentId: string | null;
      configVersion: number;
      resourceVersion?: string;
      resourceManifestId?: string;
      uiVersion?: string;
      uiManifestId?: string;
      firmwareVersion?: string;
      firmwareManifestId?: string;
    };

export interface DeviceActivationResult {
  deviceId: string;
  agentId: string | null;
  token: string;
  websocketUrl: string;
  configVersion: number;
}

interface MutationContext {
  principal: Principal;
  requestId: string;
}

interface AgentInput {
  name: string;
  defaultLocale: string;
  interactionMode: AgentRecord["interactionMode"];
  persona: string;
  draftConfig?: Record<string, unknown>;
}

interface AgentPatch {
  name?: string;
  defaultLocale?: string;
  interactionMode?: AgentRecord["interactionMode"];
  persona?: string;
  draftConfig?: Record<string, unknown>;
}

interface ProviderInput {
  kind: ProviderRecord["kind"];
  adapter: string;
  model: string;
  baseUrl?: string;
  secret?: string;
  enabled: boolean;
  priority?: number;
  locales?: string[];
}

interface ProviderPatch {
  adapter?: string;
  model?: string;
  baseUrl?: string | null;
  enabled?: boolean;
  priority?: number;
  locales?: string[];
  secretAction?: "keep" | "rotate" | "clear";
  secret?: string;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function firstInteger(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (
      typeof value === "number" &&
      Number.isInteger(value) &&
      value >= -840 &&
      value <= 840
    ) {
      return value;
    }
  }
  return undefined;
}

function desiredAgentConfigVersion(
  state: unknown,
  publishedFallback: number,
): number {
  const value = recordValue(state).agentConfigVersion;
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value > 0 &&
    value <= 2_147_483_647
    ? value
    : publishedFallback;
}

const interactionModeToDatabase: Record<AgentRecord["interactionMode"], InteractionMode> = {
  auto: InteractionMode.AUTO,
  manual: InteractionMode.MANUAL,
  realtime: InteractionMode.REALTIME,
};

const providerKindToDatabase: Record<ProviderRecord["kind"], ProviderKind> = {
  vad: ProviderKind.VAD,
  asr: ProviderKind.ASR,
  llm: ProviderKind.LLM,
  tts: ProviderKind.TTS,
  realtime: ProviderKind.REALTIME,
  memory: ProviderKind.MEMORY,
};

const providerKindToVoiceComponent = new Map<ProviderKind, VoiceRuntimeComponent>([
  [ProviderKind.VAD, "vad"],
  [ProviderKind.ASR, "asr"],
  [ProviderKind.TTS, "tts"],
]);

@Injectable()
export class ControlPlaneStore {
  constructor(
    private readonly prisma: PrismaService,
    private readonly redis: RedisService,
    private readonly pairing: PairingService,
    private readonly audit: AuditService,
    private readonly secretCrypto: SecretCryptoService,
  ) {}

  async createPairingCode(hardwareId: string): Promise<{
    code: string;
    challenge: string;
    expiresAt: string;
  }> {
    return this.pairing.create(hardwareId);
  }

  async bootstrapDevice(
    hardwareId: string,
    token?: string,
    firmwareVersion?: string,
  ): Promise<DeviceBootstrapResult> {
    const device = await this.prisma.device.findUnique({
      where: { hardwareId },
      include: { agent: true, desiredState: true },
    });
    if (!device) {
      if (token) throw new UnauthorizedException("Device token is invalid");
      return {
        state: "unbound",
        activation: await this.pairing.create(hardwareId),
      };
    }
    if (!device.tokenHash) return { state: "pending_activation" };
    if (!token || !this.tokenMatches(token, device.tokenHash)) {
      throw new UnauthorizedException("Device token is invalid");
    }

    await this.prisma.device.update({
      where: { id: device.id },
      data: {
        lastSeenAt: new Date(),
        ...(firmwareVersion ? { firmwareVersion } : {}),
      },
    });
    const desired = (device.desiredState?.state ?? {}) as Record<string, unknown>;
    const resourceVersion = desired.resourceBundleVersion;
    const resourceManifestId = desired.resourceManifestId;
    const uiVersion = desired.uiPackVersion;
    const uiManifestId = desired.uiManifestId;
    const desiredFirmwareVersion = desired.firmwareVersion;
    const firmwareManifestId = desired.firmwareManifestId;
    return {
      state: "active",
      deviceId: device.id,
      agentId: device.agentId,
      configVersion: desiredAgentConfigVersion(
        device.desiredState?.state,
        device.agent?.publishedVersion ?? 0,
      ),
      ...(typeof resourceVersion === "string" ? { resourceVersion } : {}),
      ...(typeof resourceManifestId === "string" ? { resourceManifestId } : {}),
      ...(typeof uiVersion === "string" ? { uiVersion } : {}),
      ...(typeof uiManifestId === "string" ? { uiManifestId } : {}),
      ...(typeof desiredFirmwareVersion === "string"
        ? { firmwareVersion: desiredFirmwareVersion }
        : {}),
      ...(typeof firmwareManifestId === "string" ? { firmwareManifestId } : {}),
    };
  }

  async claimPairing(
    code: string,
    name: string,
    context: MutationContext,
    agentId?: string,
  ): Promise<DeviceRecord> {
    const ticket = await this.pairing.consume(code, context.principal.userId);
    try {
      return await this.prisma.$transaction(async (transaction) => {
        if (agentId) {
          const agent = await transaction.agent.findFirst({
            where: { id: agentId, tenantId: context.principal.tenantId },
          });
          if (!agent) throw new NotFoundException("Agent not found");
          if (agent.publishedVersion <= 0) {
            throw new BadRequestException("Device agent must have a published config");
          }
        }
        const existing = await transaction.device.findUnique({ where: { hardwareId: ticket.hardwareId } });
        if (existing) throw new ConflictException("Device is already paired");
        const agent = agentId
          ? await transaction.agent.findUnique({ where: { id: agentId } })
          : null;
        const desiredState = agent
          ? { agentConfigVersion: agent.publishedVersion, agentId: agent.id }
          : {};
        const device = await transaction.device.create({
          data: {
            tenantId: context.principal.tenantId,
            hardwareId: ticket.hardwareId,
            name,
            ...(agentId ? { agentId } : {}),
            activationChallengeHash: this.hashToken(ticket.challenge),
            desiredState: {
              create: { version: 1, state: desiredState as Prisma.InputJsonValue },
            },
            reportedState: { create: { version: 0, state: {} } },
          },
          include: { desiredState: true, reportedState: true },
        });
        await this.audit.record(
          {
            tenantId: context.principal.tenantId,
            actorUserId: context.principal.userId,
            action: "device.pair",
            targetType: "device",
            targetId: device.id,
            requestId: context.requestId,
            after: { hardwareId: device.hardwareId, agentId: device.agentId },
          },
          transaction,
        );
        return this.toDeviceRecord(device);
      });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2002") {
        throw new ConflictException("Device is already paired");
      }
      throw error;
    }
  }

  async activateDevice(
    hardwareId: string,
    challenge: string,
  ): Promise<DeviceActivationResult | null> {
    const device = await this.prisma.device.findUnique({
      where: { hardwareId },
      include: { agent: true, desiredState: true },
    });
    if (!device) return null;

    const token = this.deviceToken(hardwareId, challenge);
    if (device.tokenHash && this.tokenMatches(token, device.tokenHash)) {
      return this.activationResult(device, token);
    }
    if (!device.activationChallengeHash ||
        !this.tokenMatches(challenge, device.activationChallengeHash)) {
      throw new UnauthorizedException("Activation challenge is invalid");
    }
    const activated = await this.prisma.device.updateMany({
      where: {
        id: device.id,
        activationChallengeHash: device.activationChallengeHash,
      },
      data: { tokenHash: this.hashToken(token), activationChallengeHash: null },
    });
    if (activated.count !== 1) {
      const winner = await this.prisma.device.findUnique({ where: { id: device.id } });
      if (!winner?.tokenHash || !this.tokenMatches(token, winner.tokenHash)) {
        throw new UnauthorizedException("Activation challenge is invalid");
      }
    }
    return this.activationResult(device, token);
  }

  async authenticateDevice(deviceId: string, token: string): Promise<{ id: string; tenantId: string }> {
    const device = await this.prisma.device.findUnique({ where: { id: deviceId } });
    if (!device?.tokenHash || !this.tokenMatches(token, device.tokenHash)) {
      throw new UnauthorizedException("Device token is invalid");
    }
    return { id: device.id, tenantId: device.tenantId };
  }

  async authenticateDeviceByHardware(
    hardwareId: string,
    token: string,
  ): Promise<{
    deviceId: string;
    tenantId: string;
    agentId: string | null;
    configVersion: number;
    deviceLocale?: string;
    deviceTimeZone?: string;
    deviceTimeZoneOffsetMinutes?: number;
  }> {
    const device = await this.prisma.device.findUnique({
      where: { hardwareId },
      include: { agent: true, desiredState: true, reportedState: true },
    });
    if (!device?.tokenHash || !this.tokenMatches(token, device.tokenHash)) {
      throw new UnauthorizedException("Device token is invalid");
    }
    const reported = recordValue(device.reportedState?.state);
    const locale = firstString(
      reported.locale,
      recordValue(reported.identity).locale,
      recordValue(reported.device).locale,
    );
    const timeZone = firstString(
      reported.timeZone,
      reported.timezone,
      recordValue(reported.device).timeZone,
      recordValue(reported.device).timezone,
    );
    const timeZoneOffsetMinutes = firstInteger(
      reported.timeZoneOffsetMinutes,
      reported.timezoneOffsetMinutes,
      reported.timezone_offset,
      recordValue(reported.device).timeZoneOffsetMinutes,
      recordValue(reported.device).timezoneOffsetMinutes,
    );
    return {
      deviceId: device.id,
      tenantId: device.tenantId,
      agentId: device.agentId,
      configVersion: desiredAgentConfigVersion(
        device.desiredState?.state,
        device.agent?.publishedVersion ?? 0,
      ),
      ...(locale ? { deviceLocale: locale } : {}),
      ...(timeZone ? { deviceTimeZone: timeZone } : {}),
      ...(timeZoneOffsetMinutes !== undefined
        ? { deviceTimeZoneOffsetMinutes: timeZoneOffsetMinutes }
        : {}),
    };
  }

  async listDevices(tenantId: string): Promise<DeviceRecord[]> {
    const devices = await this.prisma.device.findMany({
      where: { tenantId },
      include: { desiredState: true, reportedState: true },
      orderBy: { pairedAt: "desc" },
    });
    return devices.map((device) => this.toDeviceRecord(device));
  }

  async ingestConversationEvents(
    deviceId: string,
    events: ConversationEventInput[],
  ): Promise<{ accepted: number }> {
    const device = await this.prisma.device.findUnique({
      where: { id: deviceId },
      select: { id: true, tenantId: true, agentId: true },
    });
    if (!device) throw new NotFoundException("Device not found");
    const retentionUntil = new Date(
      Date.now() + this.conversationRetentionDays() * 24 * 60 * 60 * 1_000,
    );
    const result = await this.prisma.conversationEvent.createMany({
      data: events.map((event) => ({
        id: event.eventId,
        tenantId: device.tenantId,
        deviceId: device.id,
        ...(device.agentId ? { agentId: device.agentId } : {}),
        sessionId: event.sessionId,
        ...(event.turnId ? { turnId: event.turnId } : {}),
        generation: event.generation,
        eventType: event.eventType,
        payload: event.payload as Prisma.InputJsonValue,
        occurredAt: new Date(event.occurredAt),
        retentionUntil,
      })),
      skipDuplicates: true,
    });
    await this.prisma.conversationEvent.deleteMany({
      where: { retentionUntil: { lt: new Date() } },
    });
    return { accepted: result.count };
  }

  async listConversationEvents(
    tenantId: string,
    deviceId: string | undefined,
    limit: number,
  ): Promise<ConversationEventRecord[]> {
    const events = await this.prisma.conversationEvent.findMany({
      where: {
        tenantId,
        ...(deviceId ? { deviceId } : {}),
        retentionUntil: { gt: new Date() },
      },
      orderBy: { occurredAt: "desc" },
      take: limit,
    });
    return events.reverse().map((event) => ({
      id: event.id,
      deviceId: event.deviceId,
      ...(event.agentId ? { agentId: event.agentId } : {}),
      sessionId: event.sessionId,
      ...(event.turnId ? { turnId: event.turnId } : {}),
      generation: event.generation,
      eventType: event.eventType,
      payload: event.payload as Record<string, unknown>,
      occurredAt: event.occurredAt.toISOString(),
    }));
  }

  async listAuditEvents(
    tenantId: string,
    input: { limit: number; action?: string; targetType?: string },
  ): Promise<AuditEventRecord[]> {
    const events = await this.prisma.auditEvent.findMany({
      where: {
        tenantId,
        ...(input.action ? { action: { contains: input.action, mode: "insensitive" } } : {}),
        ...(input.targetType ? { targetType: input.targetType } : {}),
      },
      include: { actor: { select: { displayName: true } } },
      orderBy: { createdAt: "desc" },
      take: input.limit,
    });
    return events.map((event) => ({
      id: event.id,
      action: event.action,
      targetType: event.targetType,
      targetId: event.targetId,
      requestId: event.requestId,
      ...(event.beforeHash ? { beforeHash: event.beforeHash } : {}),
      ...(event.afterHash ? { afterHash: event.afterHash } : {}),
      details: this.redactedAuditDetails(event.details),
      ...(event.actor?.displayName ? { actorName: event.actor.displayName } : {}),
      createdAt: event.createdAt.toISOString(),
    }));
  }

  private redactedAuditDetails(value: Prisma.JsonValue | null): Record<string, unknown> {
    const redacted = this.redactAuditValue(value);
    if (!redacted || typeof redacted !== "object" || Array.isArray(redacted)) return {};
    return redacted as Record<string, unknown>;
  }

  private redactAuditValue(value: Prisma.JsonValue): Prisma.JsonValue | undefined {
    if (Array.isArray(value)) {
      return value
        .map((item) => this.redactAuditValue(item))
        .filter((item): item is Prisma.JsonValue => item !== undefined);
    }
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value).flatMap(([key, child]) => {
          if (/(secret|token|password|audio|transcript|argument)/i.test(key)) return [];
          if (child === undefined) return [];
          const redacted = this.redactAuditValue(child);
          return redacted === undefined ? [] : [[key, redacted]];
        }),
      );
    }
    return value;
  }

  async device(tenantId: string, id: string): Promise<DeviceRecord> {
    const device = await this.prisma.device.findFirst({
      where: { id, tenantId },
      include: { desiredState: true, reportedState: true },
    });
    if (!device) throw new NotFoundException("Device not found");
    return this.toDeviceRecord(device);
  }

  async deviceForAuthenticatedDevice(id: string): Promise<DeviceRecord> {
    const device = await this.prisma.device.findUnique({
      where: { id },
      include: { desiredState: true, reportedState: true },
    });
    if (!device) throw new NotFoundException("Device not found");
    return this.toDeviceRecord(device);
  }

  async updateReportedState(
    id: string,
    version: number,
    state: Record<string, unknown>,
    bootId?: string,
  ): Promise<DeviceRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const advanced = await transaction.deviceReportedState.updateMany({
        where: { deviceId: id, version: { lt: version } },
        data: {
          version,
          state: state as Prisma.InputJsonValue,
          ...(bootId ? { bootId } : {}),
        },
      });
      if (advanced.count !== 1) {
        const current = await transaction.deviceReportedState.findUnique({
          where: { deviceId: id },
        });
        if (!current) throw new NotFoundException("Device not found");
        if (version < current.version) {
          throw new ConflictException("Reported state version is stale");
        }
        // Equal versions are idempotent retries and must not mutate the stored state.
      } else {
        const resource = state.resource;
        if (resource && typeof resource === "object" && !Array.isArray(resource)) {
          const report = resource as Record<string, unknown>;
          const desiredVersion =
            typeof report.desiredVersion === "string" ? report.desiredVersion : undefined;
          const rolloutStatus =
            report.phase === "active"
              ? ResourceRolloutStatus.COMPLETE
              : report.phase === "failed"
                ? ResourceRolloutStatus.FAILED
                : report.phase === "rolled_back"
                  ? ResourceRolloutStatus.ROLLED_BACK
                  : undefined;
          if (rolloutStatus) {
            await transaction.resourceRollout.updateMany({
              where: {
                deviceId: id,
                status: ResourceRolloutStatus.ACTIVE,
                ...(desiredVersion
                  ? { artifact: { is: { version: desiredVersion } } }
                  : {}),
              },
              data: { status: rolloutStatus },
            });
          }
        }
        const ui = state.ui;
        if (ui && typeof ui === "object" && !Array.isArray(ui)) {
          const report = ui as Record<string, unknown>;
          const desiredVersion =
            typeof report.desiredVersion === "string" ? report.desiredVersion : undefined;
          const rolloutStatus =
            report.phase === "active"
              ? ResourceRolloutStatus.COMPLETE
              : report.phase === "failed"
                ? ResourceRolloutStatus.FAILED
                : report.phase === "rolled_back"
                  ? ResourceRolloutStatus.ROLLED_BACK
                  : undefined;
          if (rolloutStatus) {
            await transaction.uiPackRollout.updateMany({
              where: {
                deviceId: id,
                status: ResourceRolloutStatus.ACTIVE,
                ...(desiredVersion
                  ? { artifact: { is: { version: desiredVersion } } }
                  : {}),
              },
              data: { status: rolloutStatus },
            });
          }
        }
        const firmwareOta = state.firmware_ota;
        if (
          firmwareOta &&
          typeof firmwareOta === "object" &&
          !Array.isArray(firmwareOta)
        ) {
          const report = firmwareOta as Record<string, unknown>;
          const desiredVersion =
            typeof report.desiredVersion === "string"
              ? report.desiredVersion
              : undefined;
          const identity = await transaction.device.findUnique({
            where: { id },
            select: { tenantId: true },
          });
          if (identity && desiredVersion) {
            if (report.phase === "failed" || report.phase === "rolled_back") {
              await transaction.firmwareRollout.updateMany({
                where: {
                  tenantId: identity.tenantId,
                  status: {
                    in: [
                      FirmwareRolloutStatus.RUNNING,
                      FirmwareRolloutStatus.PAUSED,
                    ],
                  },
                  selectedDeviceIds: { has: id },
                  artifact: { is: { version: desiredVersion } },
                },
                data: { status: FirmwareRolloutStatus.FAILED },
              });
            } else if (report.phase === "active") {
              const campaigns = await transaction.firmwareRollout.findMany({
                where: {
                  tenantId: identity.tenantId,
                  status: {
                    in: [
                      FirmwareRolloutStatus.RUNNING,
                      FirmwareRolloutStatus.PAUSED,
                    ],
                  },
                  selectedDeviceIds: { has: id },
                  artifact: { is: { version: desiredVersion } },
                },
                include: { artifact: true },
              });
              const devices = campaigns.length
                ? await transaction.device.findMany({
                    where: { tenantId: identity.tenantId },
                    include: { reportedState: true },
                  })
                : [];
              for (const campaign of campaigns) {
                const canaries = new Set(campaign.canaryDeviceIds);
                const canaryDevices = devices.filter((device) =>
                  canaries.has(device.id),
                );
                const canaryPassed =
                  campaign.canaryDeviceIds.length === 0 ||
                  (canaryDevices.length === campaign.canaryDeviceIds.length &&
                    canaryDevices.every((device) =>
                      this.firmwareReportedActive(
                        device.reportedState?.state,
                        campaign.artifact.version,
                      ),
                    ));
                const expected = devices.filter(
                  (device) =>
                    canaries.has(device.id) ||
                    (canaryPassed &&
                      this.firmwareBucket(`${campaign.id}:${device.id}`) <
                        campaign.percentage),
                );
                const selectedIds = new Set(campaign.selectedDeviceIds);
                const selected = devices.filter((device) =>
                  selectedIds.has(device.id),
                );
                if (
                  selected.length > 0 &&
                  expected.every((device) => selectedIds.has(device.id)) &&
                  selected.every((device) =>
                    this.firmwareReportedActive(
                      device.reportedState?.state,
                      campaign.artifact.version,
                    ),
                  )
                ) {
                  await transaction.firmwareRollout.update({
                    where: { id: campaign.id },
                    data: { status: FirmwareRolloutStatus.COMPLETED },
                  });
                }
              }
            }
          }
        }
      }
      await transaction.device.update({
        where: { id },
        data: { status: DeviceStatus.ONLINE, lastSeenAt: new Date() },
      });
      const device = await transaction.device.findUnique({
        where: { id },
        include: { desiredState: true, reportedState: true },
      });
      if (!device) throw new NotFoundException("Device not found");
      return this.toDeviceRecord(device);
    });
  }

  async setDesiredState(
    id: string,
    state: Record<string, unknown>,
    context: MutationContext,
  ): Promise<DeviceRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const device = await transaction.device.findFirst({
        where: { id, tenantId: context.principal.tenantId },
        include: { desiredState: true },
      });
      if (!device) throw new NotFoundException("Device not found");
      await transaction.deviceDesiredState.upsert({
        where: { deviceId: id },
        create: { deviceId: id, version: 1, state: state as Prisma.InputJsonValue },
        update: { version: { increment: 1 }, state: state as Prisma.InputJsonValue },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "device.desired_state.update",
          targetType: "device",
          targetId: id,
          requestId: context.requestId,
          before: device.desiredState?.state,
          after: state,
        },
        transaction,
      );
      const updated = await transaction.device.findUnique({
        where: { id },
        include: { desiredState: true, reportedState: true },
      });
      if (!updated) throw new NotFoundException("Device not found");
      return this.toDeviceRecord(updated);
    });
  }

  async assignDeviceAgent(
    id: string,
    agentId: string | undefined,
    context: MutationContext,
  ): Promise<DeviceRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const device = await transaction.device.findFirst({
        where: { id, tenantId: context.principal.tenantId },
        include: { desiredState: true },
      });
      if (!device) throw new NotFoundException("Device not found");

      let agent: { id: string; publishedVersion: number } | null = null;
      if (agentId) {
        agent = await transaction.agent.findFirst({
          where: {
            id: agentId,
            tenantId: context.principal.tenantId,
          },
          select: { id: true, publishedVersion: true },
        });
        if (!agent) throw new NotFoundException("Agent not found");
        if (agent.publishedVersion <= 0) {
          throw new BadRequestException("Device agent must have a published config");
        }
      }

      const currentState = device.desiredState?.state;
      const nextState: Record<string, unknown> =
        currentState && typeof currentState === "object" && !Array.isArray(currentState)
          ? { ...(currentState as Record<string, unknown>) }
          : {};
      if (agent) {
        nextState.agentId = agent.id;
        nextState.agentConfigVersion = agent.publishedVersion;
      } else {
        delete nextState.agentId;
        delete nextState.agentConfigVersion;
      }

      await transaction.device.update({
        where: { id },
        data: { agentId: agent?.id ?? null },
      });
      await transaction.deviceDesiredState.upsert({
        where: { deviceId: id },
        create: { deviceId: id, version: 1, state: nextState as Prisma.InputJsonValue },
        update: { version: { increment: 1 }, state: nextState as Prisma.InputJsonValue },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "device.agent.assign",
          targetType: "device",
          targetId: id,
          requestId: context.requestId,
          before: { agentId: device.agentId, desiredState: device.desiredState?.state },
          after: { agentId: agent?.id ?? null, desiredState: nextState },
        },
        transaction,
      );
      const updated = await transaction.device.findUnique({
        where: { id },
        include: { desiredState: true, reportedState: true },
      });
      if (!updated) throw new NotFoundException("Device not found");
      return this.toDeviceRecord(updated);
    });
  }

  async listAgents(tenantId: string): Promise<AgentRecord[]> {
    const agents = await this.prisma.agent.findMany({
      where: { tenantId },
      orderBy: { updatedAt: "desc" },
    });
    return agents.map((agent) => this.toAgentRecord(agent));
  }

  async createAgent(input: AgentInput, context: MutationContext): Promise<AgentRecord> {
    validateAgentDraftConfig(input.draftConfig ?? {});
    return this.prisma.$transaction(async (transaction) => {
      const agent = await transaction.agent.create({
        data: {
          tenantId: context.principal.tenantId,
          name: input.name,
          defaultLocale: input.defaultLocale,
          interactionMode: interactionModeToDatabase[input.interactionMode],
          persona: input.persona,
          draftConfig: (input.draftConfig ?? {}) as Prisma.InputJsonValue,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "agent.create",
          targetType: "agent",
          targetId: agent.id,
          requestId: context.requestId,
          after: this.toAgentRecord(agent),
        },
        transaction,
      );
      return this.toAgentRecord(agent);
    });
  }

  async updateAgent(
    id: string,
    input: AgentPatch,
    context: MutationContext,
  ): Promise<AgentRecord> {
    if (input.draftConfig !== undefined) validateAgentDraftConfig(input.draftConfig);
    return this.prisma.$transaction(async (transaction) => {
      const agent = await transaction.agent.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!agent) throw new NotFoundException("Agent not found");
      const updated = await transaction.agent.update({
        where: { id },
        data: {
          ...(input.name !== undefined ? { name: input.name } : {}),
          ...(input.defaultLocale !== undefined
            ? { defaultLocale: input.defaultLocale }
            : {}),
          ...(input.interactionMode !== undefined
            ? { interactionMode: interactionModeToDatabase[input.interactionMode] }
            : {}),
          ...(input.persona !== undefined ? { persona: input.persona } : {}),
          ...(input.draftConfig !== undefined
            ? { draftConfig: input.draftConfig as Prisma.InputJsonValue }
            : {}),
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "agent.update",
          targetType: "agent",
          targetId: id,
          requestId: context.requestId,
          before: this.toAgentRecord(agent),
          after: this.toAgentRecord(updated),
        },
        transaction,
      );
      return this.toAgentRecord(updated);
    });
  }

  async publishAgent(id: string, context: MutationContext): Promise<AgentRecord> {
    return this.prisma.$transaction(async (transaction) => {
      const agent = await transaction.agent.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!agent) throw new NotFoundException("Agent not found");
      validateAgentDraftConfig(agent.draftConfig as Record<string, unknown>);
      const providers = await transaction.providerBinding.findMany({
        where: { tenantId: context.principal.tenantId, enabled: true },
        orderBy: [{ kind: "asc" }, { priority: "asc" }, { createdAt: "asc" }],
      });
      const providerChains = expandProviderChains(
        agent.draftConfig as Record<string, unknown>,
        providers.map((provider) => this.toProviderPolicyBinding(provider)),
        agent.defaultLocale,
        agent.interactionMode.toLowerCase() as AgentRecord["interactionMode"],
      );
      const selectedProviderIds = new Set(
        providerChains.flatMap((chain) => chain.providers.map((provider) => provider.id)),
      );
      const version = agent.version + 1;
      const prompt = normalizePublishedAgentPrompt(
        (agent.draftConfig as Record<string, unknown>).prompt,
        { locale: agent.defaultLocale },
      );
      const snapshot = {
        ...(agent.draftConfig as Record<string, unknown>),
        schemaVersion: 1,
        agentId: agent.id,
        agentName: agent.name,
        version,
        defaultLocale: agent.defaultLocale,
        interactionMode: agent.interactionMode.toLowerCase(),
        persona: agent.persona,
        prompt,
        providerChains,
        providers: providers
          .filter((provider) => selectedProviderIds.has(provider.id))
          .map((provider) => this.toProviderSnapshot(provider)),
      };
      const updated = await transaction.agent.update({
        where: { id },
        data: { version, publishedVersion: version },
      });
      await transaction.agentConfigVersion.create({
        data: {
          agentId: id,
          version,
          snapshot: snapshot as unknown as Prisma.InputJsonValue,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "agent.publish",
          targetType: "agent",
          targetId: id,
          requestId: context.requestId,
          before: { publishedVersion: agent.publishedVersion },
          after: { publishedVersion: version },
        },
        transaction,
      );
      return this.toAgentRecord(updated);
    });
  }

  async getAgentConfig(agentId: string, version?: number): Promise<Record<string, unknown>> {
    const agent = await this.prisma.agent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException("Agent not found");
    const selectedVersion = version ?? agent.publishedVersion;
    const config = await this.prisma.agentConfigVersion.findUnique({
      where: { agentId_version: { agentId, version: selectedVersion } },
    });
    if (!config) throw new NotFoundException("Agent config version not found");
    return config.snapshot as Record<string, unknown>;
  }

  async listProviders(tenantId: string): Promise<ProviderRecord[]> {
    const providers = await this.prisma.providerBinding.findMany({
      where: { tenantId },
      orderBy: [{ kind: "asc" }, { priority: "asc" }, { adapter: "asc" }],
    });
    return providers.map((provider) => this.toProviderRecord(provider));
  }

  async createProvider(input: ProviderInput, context: MutationContext): Promise<ProviderRecord> {
    if (input.baseUrl) this.validateProviderUrl(input.baseUrl);
    const locales = this.validateProviderLocales(input.locales ?? ["*"]);
    return this.prisma.$transaction(async (transaction) => {
      const provider = await transaction.providerBinding.create({
        data: {
          tenantId: context.principal.tenantId,
          kind: providerKindToDatabase[input.kind],
          adapter: input.adapter,
          model: input.model,
          ...(input.baseUrl ? { baseUrl: input.baseUrl } : {}),
          ...(input.secret
            ? { secretCiphertext: this.secretCrypto.encrypt(input.secret), secretConfigured: true }
            : {}),
          enabled: input.enabled,
          priority: input.priority ?? 100,
          locales,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "provider.create",
          targetType: "provider",
          targetId: provider.id,
          requestId: context.requestId,
          after: this.toProviderRecord(provider),
        },
        transaction,
      );
      return this.toProviderRecord(provider);
    });
  }

  async updateProvider(
    id: string,
    input: ProviderPatch,
    context: MutationContext,
  ): Promise<ProviderRecord> {
    if (input.baseUrl) this.validateProviderUrl(input.baseUrl);
    const locales = input.locales
      ? this.validateProviderLocales(input.locales)
      : undefined;
    if (input.secretAction === "rotate" && !input.secret) {
      throw new BadRequestException("secret is required when secretAction is rotate");
    }
    if (input.secretAction !== "rotate" && input.secret !== undefined) {
      throw new BadRequestException("secret is only accepted when secretAction is rotate");
    }
    return this.prisma.$transaction(async (transaction) => {
      const provider = await transaction.providerBinding.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!provider) throw new NotFoundException("Provider not found");
      const secretUpdate =
        input.secretAction === "rotate"
          ? {
              secretCiphertext: this.secretCrypto.encrypt(input.secret as string),
              secretConfigured: true,
            }
          : input.secretAction === "clear"
            ? { secretCiphertext: null, secretConfigured: false }
            : {};
      const updated = await transaction.providerBinding.update({
        where: { id },
        data: {
          ...(input.adapter !== undefined ? { adapter: input.adapter } : {}),
          ...(input.model !== undefined ? { model: input.model } : {}),
          ...(input.baseUrl !== undefined ? { baseUrl: input.baseUrl } : {}),
          ...(input.enabled !== undefined ? { enabled: input.enabled } : {}),
          ...(input.priority !== undefined ? { priority: input.priority } : {}),
          ...(locales ? { locales } : {}),
          ...secretUpdate,
          health: ProviderHealth.UNKNOWN,
          healthLatencyMs: null,
          healthErrorCode: null,
          healthCheckedAt: null,
          circuitState: ProviderCircuitState.CLOSED,
          failureCount: 0,
          circuitOpenedAt: null,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action:
            input.secretAction === "rotate"
              ? "provider.secret.rotate"
              : input.secretAction === "clear"
                ? "provider.secret.clear"
                : "provider.update",
          targetType: "provider",
          targetId: id,
          requestId: context.requestId,
          before: this.toProviderRecord(provider),
          after: this.toProviderRecord(updated),
        },
        transaction,
      );
      return this.toProviderRecord(updated);
    });
  }

  async resolveProviderRuntime(ids: string[]): Promise<ProviderRuntimeRecord[]> {
    if (ids.length === 0 || ids.length > 24 || new Set(ids).size !== ids.length) {
      throw new BadRequestException("providerIds must contain 1 to 24 unique ids");
    }
    const providers = await this.prisma.providerBinding.findMany({
      where: { id: { in: ids }, enabled: true },
    });
    if (providers.length !== ids.length) {
      throw new NotFoundException("One or more provider bindings are missing or disabled");
    }
    const byId = new Map(providers.map((provider) => [provider.id, provider]));
    return ids.map((id) => {
      const provider = byId.get(id);
      if (!provider) throw new NotFoundException("Provider not found");
      return {
        id: provider.id,
        kind: provider.kind.toLowerCase() as ProviderRecord["kind"],
        adapter: provider.adapter,
        model: provider.model,
        ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
        ...(provider.secretCiphertext
          ? { secret: this.secretCrypto.decrypt(provider.secretCiphertext) }
          : {}),
        priority: provider.priority,
        locales: provider.locales,
      };
    });
  }

  async testProvider(id: string, context: MutationContext): Promise<ProviderRecord> {
    const provider = await this.prisma.providerBinding.findFirst({
      where: { id, tenantId: context.principal.tenantId },
    });
    if (!provider) throw new NotFoundException("Provider not found");
    const startedAt = Date.now();
    const result = await this.probeProvider(provider);
    const healthLatencyMs = Date.now() - startedAt;
    const failureCount =
      result.health === ProviderHealth.HEALTHY
        ? 0
        : result.health === ProviderHealth.DEGRADED
          ? provider.failureCount + 1
          : provider.failureCount;
    const circuitState =
      result.health === ProviderHealth.HEALTHY
        ? ProviderCircuitState.CLOSED
        : failureCount >= 3
          ? ProviderCircuitState.OPEN
          : provider.circuitState;
    return this.prisma.$transaction(async (transaction) => {
      const updated = await transaction.providerBinding.update({
        where: { id },
        data: {
          health: result.health,
          healthLatencyMs,
          healthErrorCode: result.errorCode,
          healthCheckedAt: new Date(),
          failureCount,
          circuitState,
          circuitOpenedAt:
            circuitState === ProviderCircuitState.OPEN
              ? provider.circuitOpenedAt ?? new Date()
              : null,
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "provider.health_test",
          targetType: "provider",
          targetId: id,
          requestId: context.requestId,
          after: {
            health: result.health.toLowerCase(),
            latencyMs: healthLatencyMs,
            errorCode: result.errorCode,
            circuitState: circuitState.toLowerCase(),
          },
        },
        transaction,
      );
      return this.toProviderRecord(updated);
    });
  }

  async health(): Promise<{ database: string; redis: string }> {
    await this.prisma.$queryRaw`SELECT 1`;
    await this.redis.client.ping();
    return { database: "ok", redis: "ok" };
  }

  private toAgentRecord(agent: {
    id: string;
    name: string;
    defaultLocale: string;
    interactionMode: InteractionMode;
    persona: string;
    draftConfig: Prisma.JsonValue;
    version: number;
    publishedVersion: number;
  }): AgentRecord {
    return {
      id: agent.id,
      name: agent.name,
      defaultLocale: agent.defaultLocale,
      interactionMode: agent.interactionMode.toLowerCase() as AgentRecord["interactionMode"],
      persona: agent.persona,
      draftConfig: agent.draftConfig as Record<string, unknown>,
      version: agent.version,
      publishedVersion: agent.publishedVersion,
    };
  }

  private firmwareBucket(value: string): number {
    return createHash("sha256").update(value).digest().readUInt32BE(0) % 100;
  }

  private firmwareReportedActive(
    value: Prisma.JsonValue | undefined,
    targetVersion: string,
  ): boolean {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const state = value as Record<string, Prisma.JsonValue>;
    const ota = state.firmware_ota;
    const firmware = state.firmware;
    if (!ota || typeof ota !== "object" || Array.isArray(ota)) return false;
    const otaState = ota as Record<string, Prisma.JsonValue>;
    const firmwareState =
      firmware && typeof firmware === "object" && !Array.isArray(firmware)
        ? firmware as Record<string, Prisma.JsonValue>
        : undefined;
    return otaState.phase === "active" &&
      (otaState.currentVersion === targetVersion ||
        firmwareState?.version === targetVersion);
  }

  private toDeviceRecord(device: {
    id: string;
    hardwareId: string;
    name: string;
    status: DeviceStatus;
    agentId: string | null;
    firmwareVersion: string | null;
    pairedAt: Date;
    lastSeenAt: Date | null;
    desiredState: { version: number; state: Prisma.JsonValue } | null;
    reportedState: { version: number; state: Prisma.JsonValue; bootId: string | null } | null;
  }): DeviceRecord {
    return {
      id: device.id,
      hardwareId: device.hardwareId,
      name: device.name,
      status: device.status.toLowerCase() as DeviceRecord["status"],
      ...(device.agentId ? { agentId: device.agentId } : {}),
      ...(device.firmwareVersion ? { firmwareVersion: device.firmwareVersion } : {}),
      desiredState: {
        version: device.desiredState?.version ?? 0,
        state: (device.desiredState?.state ?? {}) as Record<string, unknown>,
      },
      reportedState: {
        version: device.reportedState?.version ?? 0,
        state: (device.reportedState?.state ?? {}) as Record<string, unknown>,
        ...(device.reportedState?.bootId ? { bootId: device.reportedState.bootId } : {}),
      },
      pairedAt: device.pairedAt.toISOString(),
      ...(device.lastSeenAt ? { lastSeenAt: device.lastSeenAt.toISOString() } : {}),
    };
  }

  private toProviderRecord(provider: {
    id: string;
    kind: ProviderKind;
    adapter: string;
    model: string;
    baseUrl: string | null;
    secretConfigured: boolean;
    enabled: boolean;
    priority: number;
    locales: string[];
    health: ProviderHealth;
    healthLatencyMs: number | null;
    healthErrorCode: string | null;
    healthCheckedAt: Date | null;
    circuitState: ProviderCircuitState;
    failureCount: number;
  }): ProviderRecord {
    return {
      id: provider.id,
      kind: provider.kind.toLowerCase() as ProviderRecord["kind"],
      adapter: provider.adapter,
      model: provider.model,
      ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
      secretConfigured: provider.secretConfigured,
      enabled: provider.enabled,
      priority: provider.priority,
      locales: provider.locales,
      health: provider.health.toLowerCase() as ProviderRecord["health"],
      ...(provider.healthLatencyMs !== null ? { healthLatencyMs: provider.healthLatencyMs } : {}),
      ...(provider.healthErrorCode ? { healthErrorCode: provider.healthErrorCode } : {}),
      ...(provider.healthCheckedAt
        ? { healthCheckedAt: provider.healthCheckedAt.toISOString() }
        : {}),
      circuitState: provider.circuitState.toLowerCase() as ProviderRecord["circuitState"],
      failureCount: provider.failureCount,
    };
  }

  private toProviderPolicyBinding(provider: {
    id: string;
    kind: ProviderKind;
    adapter: string;
    model: string;
    baseUrl: string | null;
    secretConfigured: boolean;
    enabled: boolean;
    priority: number;
    locales: string[];
  }): ProviderPolicyBinding {
    return {
      id: provider.id,
      kind: provider.kind.toLowerCase() as ProviderRecord["kind"],
      adapter: provider.adapter,
      model: provider.model,
      ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
      secretConfigured: provider.secretConfigured,
      enabled: provider.enabled,
      priority: provider.priority,
      locales: provider.locales,
    };
  }

  private toProviderSnapshot(provider: Parameters<ControlPlaneStore["toProviderPolicyBinding"]>[0]) {
    const snapshot = this.toProviderPolicyBinding(provider);
    const { enabled: _enabled, ...published } = snapshot;
    return published;
  }

  private validateProviderLocales(locales: string[]): string[] {
    const normalized = [...new Set(locales.map((locale) => locale.trim()))];
    if (normalized.length === 0 || normalized.length > 16 || normalized.some((locale) => !locale)) {
      throw new BadRequestException("Provider locales must contain 1 to 16 unique values");
    }
    for (const locale of normalized) {
      if (locale === "*") continue;
      try {
        if (new Intl.Locale(locale).toString() !== locale) throw new Error("not canonical");
      } catch {
        throw new BadRequestException(`Provider locale ${locale} is invalid or not canonical`);
      }
    }
    return normalized;
  }

  private async probeProvider(provider: {
    enabled: boolean;
    kind: ProviderKind;
    adapter: string;
    model: string;
    baseUrl: string | null;
    secretCiphertext: string | null;
  }): Promise<{ health: ProviderHealth; errorCode: string | null }> {
    if (!provider.enabled) {
      return { health: ProviderHealth.DEGRADED, errorCode: "disabled" };
    }
    if (!provider.baseUrl) {
      const componentName = providerKindToVoiceComponent.get(provider.kind);
      if (!componentName) {
        return { health: ProviderHealth.UNKNOWN, errorCode: "runtime_probe_unavailable" };
      }
      const result = await probeVoiceRuntimeComponent(
        componentName,
        process.env.VEETEE_VOICE_INTERNAL_URL ?? "http://127.0.0.1:8000",
      );
      return {
        health: result.healthy ? ProviderHealth.HEALTHY : ProviderHealth.DEGRADED,
        errorCode: result.errorCode,
      };
    }
    this.validateProviderUrl(provider.baseUrl);
    const headers: Record<string, string> = { accept: "application/json" };
    if (provider.secretCiphertext) {
      headers.authorization = `Bearer ${this.secretCrypto.decrypt(provider.secretCiphertext)}`;
    }
    try {
      const baseUrl = provider.baseUrl.replace(/\/$/, "");
      const response =
        provider.kind === ProviderKind.LLM && provider.adapter.includes("openai-compatible")
          ? await fetch(`${baseUrl}/chat/completions`, {
              method: "POST",
              headers: { ...headers, "content-type": "application/json" },
              body: JSON.stringify({
                model: provider.model,
                stream: false,
                max_tokens: 4,
                reasoning_effort: "none",
                messages: [{ role: "user", content: "Reply with OK." }],
              }),
              signal: AbortSignal.timeout(8_000),
            })
          : await fetch(`${baseUrl}/models`, {
              headers,
              signal: AbortSignal.timeout(3_000),
            });
      return response.ok
        ? { health: ProviderHealth.HEALTHY, errorCode: null }
        : { health: ProviderHealth.DEGRADED, errorCode: `http_${response.status}` };
    } catch (error) {
      return {
        health: ProviderHealth.DEGRADED,
        errorCode: error instanceof DOMException && error.name === "TimeoutError" ? "timeout" : "unreachable",
      };
    }
  }

  private hashToken(token: string): string {
    return createHash("sha256").update(token).digest("hex");
  }

  private deviceToken(hardwareId: string, challenge: string): string {
    const secret = process.env.VEETEE_DEVICE_TOKEN_SECRET ?? process.env.VEETEE_AUTH_SECRET;
    if (!secret || secret.length < 32) {
      throw new Error("VEETEE_DEVICE_TOKEN_SECRET or VEETEE_AUTH_SECRET must contain at least 32 characters");
    }
    return createHmac("sha256", secret)
      .update("veetee-device-token-v1\0")
      .update(hardwareId)
      .update("\0")
      .update(challenge)
      .digest("base64url");
  }

  private activationResult(
    device: {
      id: string;
      agentId: string | null;
      agent: { publishedVersion: number } | null;
      desiredState: { state: Prisma.JsonValue } | null;
    },
    token: string,
  ): DeviceActivationResult {
    return {
      deviceId: device.id,
      agentId: device.agentId,
      token,
      websocketUrl: process.env.VEETEE_VOICE_WS_URL ?? "ws://127.0.0.1:8000/veetee/v1/",
      configVersion: desiredAgentConfigVersion(
        device.desiredState?.state,
        device.agent?.publishedVersion ?? 0,
      ),
    };
  }

  private tokenMatches(token: string, expectedHash: string): boolean {
    const actual = Buffer.from(this.hashToken(token));
    const expected = Buffer.from(expectedHash);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  }

  private validateProviderUrl(value: string): void {
    const url = new URL(value);
    const localHost = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && localHost)) {
      throw new BadRequestException("Provider URL must use HTTPS or loopback HTTP");
    }
  }

  private conversationRetentionDays(): number {
    const value = Number(process.env.VEETEE_CONVERSATION_EVENT_RETENTION_DAYS ?? 7);
    if (!Number.isInteger(value) || value < 1 || value > 30) {
      throw new Error(
        "VEETEE_CONVERSATION_EVENT_RETENTION_DAYS must be an integer from 1 to 30",
      );
    }
    return value;
  }
}
