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
  Prisma,
  ProviderHealth,
  ProviderKind,
} from "@prisma/client";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";
import { RedisService } from "../database/redis.service.js";
import { PairingService } from "../pairing/pairing.service.js";
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
  health: "unknown" | "healthy" | "degraded";
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
    return {
      state: "active",
      deviceId: device.id,
      agentId: device.agentId,
      configVersion: device.agent?.publishedVersion ?? 0,
      ...(typeof resourceVersion === "string" ? { resourceVersion } : {}),
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
      include: { agent: true },
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
  }> {
    const device = await this.prisma.device.findUnique({
      where: { hardwareId },
      include: { agent: true },
    });
    if (!device?.tokenHash || !this.tokenMatches(token, device.tokenHash)) {
      throw new UnauthorizedException("Device token is invalid");
    }
    return {
      deviceId: device.id,
      tenantId: device.tenantId,
      agentId: device.agentId,
      configVersion: device.agent?.publishedVersion ?? 0,
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

  async listAgents(tenantId: string): Promise<AgentRecord[]> {
    const agents = await this.prisma.agent.findMany({
      where: { tenantId },
      orderBy: { updatedAt: "desc" },
    });
    return agents.map((agent) => this.toAgentRecord(agent));
  }

  async createAgent(input: AgentInput, context: MutationContext): Promise<AgentRecord> {
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
      const providers = await transaction.providerBinding.findMany({
        where: { tenantId: context.principal.tenantId, enabled: true },
      });
      const version = agent.version + 1;
      const snapshot = {
        ...(agent.draftConfig as Record<string, unknown>),
        schemaVersion: 1,
        agentId: agent.id,
        version,
        defaultLocale: agent.defaultLocale,
        interactionMode: agent.interactionMode.toLowerCase(),
        persona: agent.persona,
        providers: providers.map((provider) => ({
          id: provider.id,
          kind: provider.kind.toLowerCase(),
          adapter: provider.adapter,
          model: provider.model,
          ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
          secretConfigured: provider.secretConfigured,
        })),
      };
      const updated = await transaction.agent.update({
        where: { id },
        data: { version, publishedVersion: version },
      });
      await transaction.agentConfigVersion.create({
        data: { agentId: id, version, snapshot: snapshot as Prisma.InputJsonValue },
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
      orderBy: [{ kind: "asc" }, { adapter: "asc" }],
    });
    return providers.map((provider) => this.toProviderRecord(provider));
  }

  async createProvider(input: ProviderInput, context: MutationContext): Promise<ProviderRecord> {
    if (input.baseUrl) this.validateProviderUrl(input.baseUrl);
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

  async testProvider(id: string, context: MutationContext): Promise<ProviderRecord> {
    const provider = await this.prisma.providerBinding.findFirst({
      where: { id, tenantId: context.principal.tenantId },
    });
    if (!provider) throw new NotFoundException("Provider not found");
    let health = provider.enabled ? ProviderHealth.HEALTHY : ProviderHealth.DEGRADED;
    if (provider.enabled && provider.baseUrl) {
      this.validateProviderUrl(provider.baseUrl);
      try {
        const headers: Record<string, string> = {};
        if (provider.secretCiphertext) {
          headers.authorization = `Bearer ${this.secretCrypto.decrypt(provider.secretCiphertext)}`;
        }
        const response = await fetch(`${provider.baseUrl.replace(/\/$/, "")}/models`, {
          headers,
          signal: AbortSignal.timeout(3_000),
        });
        health = response.ok ? ProviderHealth.HEALTHY : ProviderHealth.DEGRADED;
      } catch {
        health = ProviderHealth.DEGRADED;
      }
    }
    return this.prisma.$transaction(async (transaction) => {
      const updated = await transaction.providerBinding.update({
        where: { id },
        data: { health },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "provider.health_test",
          targetType: "provider",
          targetId: id,
          requestId: context.requestId,
          after: { health: health.toLowerCase() },
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

  private toDeviceRecord(device: {
    id: string;
    hardwareId: string;
    name: string;
    status: DeviceStatus;
    agentId: string | null;
    firmwareVersion: string | null;
    pairedAt: Date;
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
    health: ProviderHealth;
  }): ProviderRecord {
    return {
      id: provider.id,
      kind: provider.kind.toLowerCase() as ProviderRecord["kind"],
      adapter: provider.adapter,
      model: provider.model,
      ...(provider.baseUrl ? { baseUrl: provider.baseUrl } : {}),
      secretConfigured: provider.secretConfigured,
      enabled: provider.enabled,
      health: provider.health.toLowerCase() as ProviderRecord["health"],
    };
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
    device: { id: string; agentId: string | null; agent: { publishedVersion: number } | null },
    token: string,
  ): DeviceActivationResult {
    return {
      deviceId: device.id,
      agentId: device.agentId,
      token,
      websocketUrl: process.env.VEETEE_VOICE_WS_URL ?? "ws://127.0.0.1:8000/xiaozhi/v1/",
      configVersion: device.agent?.publishedVersion ?? 0,
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
}
