import { lookup } from "node:dns/promises";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { BlockList, isIP } from "node:net";
import { performance } from "node:perf_hooks";

import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import {
  Prisma,
  RemoteMcpAuthType,
  RemoteMcpCallActor,
  RemoteMcpCallStatus,
  RemoteMcpHealth,
  RemoteMcpNetworkPolicy,
  RemoteMcpTransport,
} from "@prisma/client";

import { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import { PrismaService } from "../database/prisma.service.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";

export type RemoteMcpTransportValue = "streamable_http" | "sse";
export type RemoteMcpAuthTypeValue = "none" | "bearer" | "header";
export type RemoteMcpNetworkPolicyValue = "public_only" | "private_allowlist";
export type RemoteMcpHealthValue = "unknown" | "healthy" | "degraded";
export type RemoteMcpSafetyClass =
  | "read_only"
  | "reversible"
  | "disruptive"
  | "destructive";

export interface RemoteMcpToolPolicy {
  name: string;
  safetyClass: RemoteMcpSafetyClass;
  requiresConfirmation: boolean;
}

export interface RemoteMcpEndpointInput {
  name: string;
  url: string;
  transport: RemoteMcpTransportValue;
  authType: RemoteMcpAuthTypeValue;
  authHeaderName?: string;
  secret?: string;
  timeoutSeconds: number;
  resultMaxBytes: number;
  networkPolicy: RemoteMcpNetworkPolicyValue;
  allowedHosts: string[];
  tools: RemoteMcpToolPolicy[];
}

export interface RemoteMcpEndpointRecord {
  id: string;
  name: string;
  url: string;
  transport: RemoteMcpTransportValue;
  enabled: boolean;
  authType: RemoteMcpAuthTypeValue;
  authHeaderName?: string;
  secretConfigured: boolean;
  timeoutSeconds: number;
  resultMaxBytes: number;
  networkPolicy: RemoteMcpNetworkPolicyValue;
  allowedHosts: string[];
  tools: RemoteMcpToolPolicy[];
  health: RemoteMcpHealthValue;
  healthLatencyMs?: number;
  healthErrorCode?: string;
  healthCheckedAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface RemoteMcpAssignmentInput {
  endpointId: string;
  toolNames: string[];
  timeoutSeconds: number;
}

export interface RemoteMcpAssignmentRecord {
  endpointId: string;
  endpointName: string;
  enabled: boolean;
  toolNames: string[];
  timeoutSeconds: number;
  health: RemoteMcpHealthValue;
}

export interface RemoteMcpAuditInput {
  eventId: string;
  endpointId: string;
  agentId: string;
  deviceId: string;
  configVersion: number;
  sessionId: string;
  turnId: string;
  toolName: string;
  argumentsHash: string;
  status: "succeeded" | "failed" | "cancelled" | "stale" | "completed_after_abort";
  durationMs: number;
  actor: "model" | "user" | "system";
  occurredAt: string;
}

interface MutationContext {
  principal: Principal;
  requestId: string;
}

const transportToDatabase: Record<RemoteMcpTransportValue, RemoteMcpTransport> = {
  streamable_http: RemoteMcpTransport.STREAMABLE_HTTP,
  sse: RemoteMcpTransport.SSE,
};
const authTypeToDatabase: Record<RemoteMcpAuthTypeValue, RemoteMcpAuthType> = {
  none: RemoteMcpAuthType.NONE,
  bearer: RemoteMcpAuthType.BEARER,
  header: RemoteMcpAuthType.HEADER,
};
const networkPolicyToDatabase: Record<RemoteMcpNetworkPolicyValue, RemoteMcpNetworkPolicy> = {
  public_only: RemoteMcpNetworkPolicy.PUBLIC_ONLY,
  private_allowlist: RemoteMcpNetworkPolicy.PRIVATE_ALLOWLIST,
};
const statusToDatabase: Record<RemoteMcpAuditInput["status"], RemoteMcpCallStatus> = {
  succeeded: RemoteMcpCallStatus.SUCCEEDED,
  failed: RemoteMcpCallStatus.FAILED,
  cancelled: RemoteMcpCallStatus.CANCELLED,
  stale: RemoteMcpCallStatus.STALE,
  completed_after_abort: RemoteMcpCallStatus.COMPLETED_AFTER_ABORT,
};
const actorToDatabase: Record<RemoteMcpAuditInput["actor"], RemoteMcpCallActor> = {
  model: RemoteMcpCallActor.MODEL,
  user: RemoteMcpCallActor.USER,
  system: RemoteMcpCallActor.SYSTEM,
};

const forbiddenHeaderNames = new Set([
  "accept",
  "authorization",
  "connection",
  "content-length",
  "content-type",
  "cookie",
  "forwarded",
  "host",
  "mcp-protocol-version",
  "mcp-session-id",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
const metadataHostnames = new Set([
  "instance-data",
  "instance-data.ec2.internal",
  "metadata",
  "metadata.google.internal",
  "metadata.google.internal.",
]);

type IpFamily = "ipv4" | "ipv6";
type IpSubnet = readonly [network: string, prefix: number, family: IpFamily];

function subnetList(subnets: readonly IpSubnet[]): BlockList {
  const list = new BlockList();
  for (const [network, prefix, family] of subnets) {
    list.addSubnet(network, prefix, family);
  }
  return list;
}

function canonicalHostname(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/\.$/u, "");
  return normalized.startsWith("[") && normalized.endsWith("]")
    ? normalized.slice(1, -1)
    : normalized;
}

// Accept only globally routable peers for public_only. Translation/tunnel ranges are
// blocked because their embedded IPv4 destination cannot be proven safe at this layer.
const forbiddenIpv4 = subnetList([
  ["0.0.0.0", 8, "ipv4"],
  ["100.100.100.200", 32, "ipv4"],
  ["127.0.0.0", 8, "ipv4"],
  ["169.254.0.0", 16, "ipv4"],
  ["192.0.0.0", 24, "ipv4"],
  ["192.0.2.0", 24, "ipv4"],
  ["192.88.99.0", 24, "ipv4"],
  ["198.18.0.0", 15, "ipv4"],
  ["198.51.100.0", 24, "ipv4"],
  ["203.0.113.0", 24, "ipv4"],
  ["224.0.0.0", 4, "ipv4"],
  ["240.0.0.0", 4, "ipv4"],
] as const);
const privateIpv4 = subnetList([
  ["10.0.0.0", 8, "ipv4"],
  ["100.64.0.0", 10, "ipv4"],
  ["172.16.0.0", 12, "ipv4"],
  ["192.168.0.0", 16, "ipv4"],
] as const);
const forbiddenIpv6 = subnetList([
  ["::", 96, "ipv6"], // unspecified, loopback and deprecated IPv4-compatible form
  ["::ffff:0:0", 96, "ipv6"], // IPv4-mapped
  ["64:ff9b::", 96, "ipv6"], // well-known NAT64
  ["64:ff9b:1::", 48, "ipv6"], // local-use NAT64
  ["100::", 64, "ipv6"], // discard-only
  ["2001::", 23, "ipv6"], // IETF protocol assignments, including Teredo/ORCHID
  ["2001:db8::", 32, "ipv6"], // documentation
  ["2002::", 16, "ipv6"], // 6to4
  ["3fff::", 20, "ipv6"], // documentation
  ["5f00::", 16, "ipv6"], // segment-routing SID block, not a public endpoint range
  ["fd00:ec2::254", 128, "ipv6"], // AWS metadata
  ["fe80::", 10, "ipv6"], // link-local
  ["fec0::", 10, "ipv6"], // deprecated site-local
  ["ff00::", 8, "ipv6"], // multicast
] as const);
const privateIpv6 = subnetList([["fc00::", 7, "ipv6"]] as const);

@Injectable()
export class RemoteMcpService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly audit: AuditService,
    private readonly secretCrypto: SecretCryptoService,
  ) {}

  async listEndpoints(tenantId: string): Promise<RemoteMcpEndpointRecord[]> {
    const endpoints = await this.prisma.remoteMcpEndpoint.findMany({
      where: { tenantId },
      orderBy: [{ updatedAt: "desc" }, { id: "asc" }],
    });
    return endpoints.map((endpoint) => this.endpointRecord(endpoint));
  }

  async endpoint(tenantId: string, id: string): Promise<RemoteMcpEndpointRecord> {
    const endpoint = await this.prisma.remoteMcpEndpoint.findFirst({
      where: { id, tenantId },
    });
    if (!endpoint) throw new NotFoundException("Remote MCP endpoint not found");
    return this.endpointRecord(endpoint);
  }

  async createEndpoint(
    input: RemoteMcpEndpointInput,
    context: MutationContext,
  ): Promise<RemoteMcpEndpointRecord> {
    const normalized = await this.validateEndpointInput(input);
    try {
      return await this.prisma.$transaction(async (transaction) => {
        const endpoint = await transaction.remoteMcpEndpoint.create({
          data: {
            tenantId: context.principal.tenantId,
            name: input.name.trim(),
            url: normalized.url,
            transport: transportToDatabase[input.transport],
            authType: authTypeToDatabase[input.authType],
            ...(normalized.authHeaderName
              ? { authHeaderName: normalized.authHeaderName }
              : {}),
            ...(input.secret
              ? {
                  secretCiphertext: this.secretCrypto.encrypt(input.secret),
                  secretConfigured: true,
                }
              : {}),
            timeoutMs: Math.round(input.timeoutSeconds * 1_000),
            resultMaxBytes: input.resultMaxBytes,
            networkPolicy: networkPolicyToDatabase[input.networkPolicy],
            allowedHosts: normalized.allowedHosts,
            tools: normalized.tools as unknown as Prisma.InputJsonValue,
          },
        });
        const record = this.endpointRecord(endpoint);
        await this.audit.record(
          {
            tenantId: context.principal.tenantId,
            actorUserId: context.principal.userId,
            action: "remote_mcp.endpoint.create",
            targetType: "remote_mcp_endpoint",
            targetId: endpoint.id,
            requestId: context.requestId,
            after: this.endpointAuditShape(record),
          },
          transaction,
        );
        return record;
      });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2002") {
        throw new ConflictException("A Remote MCP endpoint with this name already exists");
      }
      throw error;
    }
  }

  async updateEndpoint(
    id: string,
    input: {
      enabled?: boolean;
      secretAction?: "keep" | "rotate" | "clear";
      secret?: string;
    },
    context: MutationContext,
  ): Promise<RemoteMcpEndpointRecord> {
    return this.prisma.$transaction(async (transaction) => {
      await transaction.$queryRaw(
        Prisma.sql`
          SELECT "id" FROM "RemoteMcpEndpoint"
          WHERE "id" = ${id} AND "tenantId" = ${context.principal.tenantId}
          FOR UPDATE
        `,
      );
      const endpoint = await transaction.remoteMcpEndpoint.findFirst({
        where: { id, tenantId: context.principal.tenantId },
      });
      if (!endpoint) throw new NotFoundException("Remote MCP endpoint not found");
      if (input.enabled === undefined && input.secretAction === undefined) {
        throw new BadRequestException("Remote MCP endpoint update is empty");
      }
      if (input.secretAction === "rotate" && !input.secret) {
        throw new BadRequestException("Remote MCP secret is required for rotation");
      }
      if (input.secret && /[\u0000-\u001f\u007f]/u.test(input.secret)) {
        throw new BadRequestException("Remote MCP secret contains unsupported control characters");
      }
      if (input.secretAction === "rotate" && endpoint.authType === RemoteMcpAuthType.NONE) {
        throw new BadRequestException("Remote MCP endpoint does not use authentication");
      }
      if (input.secretAction === "clear" && input.enabled === true) {
        throw new BadRequestException("Remote MCP endpoint cannot be enabled while clearing its secret");
      }
      if (
        input.enabled === true &&
        endpoint.authType !== RemoteMcpAuthType.NONE &&
        !endpoint.secretConfigured &&
        input.secretAction !== "rotate"
      ) {
        throw new BadRequestException(
          "Remote MCP endpoint credential must be configured before enabling",
        );
      }
      if (input.secretAction !== "rotate" && input.secret) {
        throw new BadRequestException("Remote MCP secret is only accepted with rotate");
      }
      const updated = await transaction.remoteMcpEndpoint.update({
        where: { id },
        data: {
          ...(input.enabled !== undefined ? { enabled: input.enabled } : {}),
          ...(input.secretAction === "rotate"
            ? {
                secretCiphertext: this.secretCrypto.encrypt(input.secret!),
                secretConfigured: true,
              }
            : {}),
          ...(input.secretAction === "clear"
            ? {
                secretCiphertext: null,
                secretConfigured: false,
                enabled: false,
              }
            : {}),
        },
      });
      const before = this.endpointRecord(endpoint);
      const after = this.endpointRecord(updated);
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "remote_mcp.endpoint.update",
          targetType: "remote_mcp_endpoint",
          targetId: id,
          requestId: context.requestId,
          before: this.endpointAuditShape(before),
          after: this.endpointAuditShape(after),
          details: {
            secretAction: input.secretAction ?? "keep",
          },
        },
        transaction,
      );
      return after;
    });
  }

  async testEndpoint(id: string, context: MutationContext): Promise<RemoteMcpEndpointRecord> {
    const endpoint = await this.prisma.remoteMcpEndpoint.findFirst({
      where: { id, tenantId: context.principal.tenantId },
    });
    if (!endpoint) throw new NotFoundException("Remote MCP endpoint not found");
    await this.audit.record({
      tenantId: context.principal.tenantId,
      actorUserId: context.principal.userId,
      action: "remote_mcp.endpoint.test.requested",
      targetType: "remote_mcp_endpoint",
      targetId: id,
      requestId: context.requestId,
      details: { transport: this.transportValue(endpoint.transport) },
    });

    const startedAt = performance.now();
    let errorCode: string | undefined;
    try {
      const deadlineAt = Date.now() + endpoint.timeoutMs;
      const target = await this.resolveSafeNetwork(
        endpoint.url,
        this.networkPolicyValue(endpoint.networkPolicy),
        endpoint.allowedHosts,
        deadlineAt,
      );
      await this.probe(endpoint, target, deadlineAt);
    } catch (error) {
      errorCode = this.probeErrorCode(error);
    }
    const latencyMs = Math.max(0, Math.round(performance.now() - startedAt));
    return this.prisma.$transaction(async (transaction) => {
      const updated = await transaction.remoteMcpEndpoint.update({
        where: { id },
        data: {
          health: errorCode ? RemoteMcpHealth.DEGRADED : RemoteMcpHealth.HEALTHY,
          healthLatencyMs: latencyMs,
          healthErrorCode: errorCode ?? null,
          healthCheckedAt: new Date(),
        },
      });
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: errorCode
            ? "remote_mcp.endpoint.test.failed"
            : "remote_mcp.endpoint.test.succeeded",
          targetType: "remote_mcp_endpoint",
          targetId: id,
          requestId: context.requestId,
          details: {
            latencyMs,
            ...(errorCode ? { errorCode } : {}),
          },
        },
        transaction,
      );
      return this.endpointRecord(updated);
    });
  }

  async listAssignments(
    tenantId: string,
    agentId: string,
  ): Promise<{ items: RemoteMcpAssignmentRecord[] }> {
    await this.requireAgent(tenantId, agentId);
    const assignments = await this.prisma.agentRemoteMcpAssignment.findMany({
      where: { tenantId, agentId, endpoint: { is: { tenantId } } },
      include: { endpoint: true },
      orderBy: [{ createdAt: "asc" }, { id: "asc" }],
    });
    return { items: assignments.map((assignment) => this.assignmentRecord(assignment)) };
  }

  async replaceAssignments(
    agentId: string,
    assignments: readonly RemoteMcpAssignmentInput[],
    context: MutationContext,
  ): Promise<{ items: RemoteMcpAssignmentRecord[] }> {
    if (new Set(assignments.map(({ endpointId }) => endpointId)).size !== assignments.length) {
      throw new BadRequestException("Remote MCP assignments contain duplicate endpoints");
    }
    const endpoints = await this.prisma.remoteMcpEndpoint.findMany({
      where: {
        tenantId: context.principal.tenantId,
        id: { in: assignments.map(({ endpointId }) => endpointId) },
      },
    });
    const endpointById = new Map(endpoints.map((endpoint) => [endpoint.id, endpoint]));
    for (const assignment of assignments) {
      const endpoint = endpointById.get(assignment.endpointId);
      if (!endpoint) throw new NotFoundException("Remote MCP endpoint not found");
      const configuredTools = new Set(this.tools(endpoint.tools).map(({ name }) => name));
      if (new Set(assignment.toolNames).size !== assignment.toolNames.length) {
        throw new BadRequestException("Remote MCP assignment contains duplicate tools");
      }
      if (!assignment.toolNames.length || assignment.toolNames.some((name) => !configuredTools.has(name))) {
        throw new BadRequestException(
          `Remote MCP assignment for ${endpoint.name} must use its explicit tool allowlist`,
        );
      }
      if (assignment.timeoutSeconds * 1_000 > endpoint.timeoutMs) {
        throw new BadRequestException(
          `Remote MCP assignment timeout cannot exceed endpoint ${endpoint.name} timeout`,
        );
      }
    }

    return this.prisma.$transaction(async (transaction) => {
      await transaction.$queryRaw(
        Prisma.sql`
          SELECT "id" FROM "Agent"
          WHERE "id" = ${agentId} AND "tenantId" = ${context.principal.tenantId}
          FOR UPDATE
        `,
      );
      const agent = await transaction.agent.findFirst({
        where: { id: agentId, tenantId: context.principal.tenantId },
        select: { id: true },
      });
      if (!agent) throw new NotFoundException("Agent not found");
      const before = await transaction.agentRemoteMcpAssignment.findMany({
        where: { tenantId: context.principal.tenantId, agentId },
        select: { endpointId: true, toolNames: true, timeoutMs: true },
      });
      await transaction.agentRemoteMcpAssignment.deleteMany({
        where: { tenantId: context.principal.tenantId, agentId },
      });
      if (assignments.length) {
        await transaction.agentRemoteMcpAssignment.createMany({
          data: assignments.map((assignment) => ({
            tenantId: context.principal.tenantId,
            agentId,
            endpointId: assignment.endpointId,
            toolNames: assignment.toolNames,
            timeoutMs: Math.round(assignment.timeoutSeconds * 1_000),
          })),
        });
      }
      await this.audit.record(
        {
          tenantId: context.principal.tenantId,
          actorUserId: context.principal.userId,
          action: "remote_mcp.assignment.replace",
          targetType: "agent",
          targetId: agentId,
          requestId: context.requestId,
          before: before.map((value) => ({
            endpointId: value.endpointId,
            toolNames: value.toolNames,
            timeoutSeconds: value.timeoutMs / 1_000,
          })),
          after: assignments,
        },
        transaction,
      );
      const stored = await transaction.agentRemoteMcpAssignment.findMany({
        where: { tenantId: context.principal.tenantId, agentId },
        include: { endpoint: true },
        orderBy: [{ createdAt: "asc" }, { id: "asc" }],
      });
      return { items: stored.map((assignment) => this.assignmentRecord(assignment)) };
    });
  }

  async resolve(
    agentId: string,
    deviceId: string,
    configVersion: number,
  ): Promise<{
    configVersion: number;
    endpoints: Array<{
      id: string;
      name: string;
      transport: RemoteMcpTransportValue;
      url: string;
      headers: Record<string, string>;
      timeoutSeconds: number;
      resultMaxBytes: number;
      networkPolicy: RemoteMcpNetworkPolicyValue;
      allowedHosts: string[];
      allowedTools: RemoteMcpToolPolicy[];
    }>;
  }> {
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
    const assignments = this.snapshotAssignments(snapshot.remoteMcpEndpoints);
    if (!assignments.length) {
      await this.auditRemoteMcpResolve(
        config.agent.tenantId,
        agentId,
        deviceId,
        configVersion,
        [],
      );
      return { configVersion, endpoints: [] };
    }
    const endpoints = await this.prisma.remoteMcpEndpoint.findMany({
      where: {
        tenantId: config.agent.tenantId,
        enabled: true,
        id: { in: assignments.map(({ endpointId }) => endpointId) },
      },
    });
    const endpointById = new Map(endpoints.map((endpoint) => [endpoint.id, endpoint]));
    const resolved: Array<{
      id: string;
      name: string;
      transport: RemoteMcpTransportValue;
      url: string;
      headers: Record<string, string>;
      timeoutSeconds: number;
      resultMaxBytes: number;
      networkPolicy: RemoteMcpNetworkPolicyValue;
      allowedHosts: string[];
      allowedTools: RemoteMcpToolPolicy[];
    }> = [];
    for (const assignment of assignments) {
      const endpoint = endpointById.get(assignment.endpointId);
      if (!endpoint) continue;
      const allowed = new Set(assignment.toolNames);
      const allowedTools = this.tools(endpoint.tools).filter(({ name }) => allowed.has(name));
      if (!allowedTools.length) continue;
      resolved.push({
        id: endpoint.id,
        name: endpoint.name,
        transport: this.transportValue(endpoint.transport),
        url: endpoint.url,
        headers: this.authHeaders(endpoint),
        timeoutSeconds: Math.min(assignment.timeoutSeconds, endpoint.timeoutMs / 1_000),
        resultMaxBytes: endpoint.resultMaxBytes,
        networkPolicy: this.networkPolicyValue(endpoint.networkPolicy),
        allowedHosts: endpoint.allowedHosts,
        allowedTools,
      });
    }
    await this.auditRemoteMcpResolve(
      config.agent.tenantId,
      agentId,
      deviceId,
      configVersion,
      resolved.map(({ id }) => id),
    );
    return { configVersion, endpoints: resolved };
  }

  async recordInvocation(
    input: RemoteMcpAuditInput,
  ): Promise<{ recorded: boolean; duplicate: boolean }> {
    const existing = await this.prisma.remoteMcpInvocation.findUnique({
      where: { id: input.eventId },
      select: { id: true },
    });
    if (existing) return { recorded: false, duplicate: true };
    const endpoint = await this.prisma.remoteMcpEndpoint.findUnique({
      where: { id: input.endpointId },
      select: { tenantId: true, tools: true },
    });
    if (!endpoint) throw new NotFoundException("Remote MCP endpoint not found");
    const [config, device] = await Promise.all([
      this.prisma.agentConfigVersion.findUnique({
        where: {
          agentId_version: { agentId: input.agentId, version: input.configVersion },
        },
        include: { agent: { select: { tenantId: true } } },
      }),
      this.prisma.device.findFirst({
        where: {
          id: input.deviceId,
          tenantId: endpoint.tenantId,
          agentId: input.agentId,
        },
        select: { id: true },
      }),
    ]);
    if (!config || config.agent.tenantId !== endpoint.tenantId || !device) {
      throw new NotFoundException("Remote MCP invocation scope not found");
    }
    const assignments = this.snapshotAssignments(
      (config.snapshot as Record<string, unknown>).remoteMcpEndpoints,
    );
    const assignment = assignments.find(({ endpointId }) => endpointId === input.endpointId);
    if (!assignment || !assignment.toolNames.includes(input.toolName)) {
      throw new ConflictException("Remote MCP tool was not assigned in this agent version");
    }
    try {
      return await this.prisma.$transaction(async (transaction) => {
        await transaction.remoteMcpInvocation.create({
          data: {
            id: input.eventId,
            tenantId: endpoint.tenantId,
            endpointId: input.endpointId,
            agentId: input.agentId,
            deviceId: input.deviceId,
            configVersion: input.configVersion,
            sessionId: input.sessionId,
            turnId: input.turnId,
            toolName: input.toolName,
            argumentsHash: input.argumentsHash,
            status: statusToDatabase[input.status],
            durationMs: input.durationMs,
            actor: actorToDatabase[input.actor],
            occurredAt: new Date(input.occurredAt),
          },
        });
        await this.audit.record(
          {
            tenantId: endpoint.tenantId,
            action: `remote_mcp.call.${input.status}`,
            targetType: "remote_mcp_endpoint",
            targetId: input.endpointId,
            requestId: input.eventId,
            details: {
              agentId: input.agentId,
              deviceId: input.deviceId,
              configVersion: input.configVersion,
              sessionId: input.sessionId,
              turnId: input.turnId,
              toolName: input.toolName,
              argumentsHash: input.argumentsHash,
              status: input.status,
              durationMs: input.durationMs,
              actor: input.actor,
            },
          },
          transaction,
        );
        return { recorded: true, duplicate: false };
      });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2002") {
        return { recorded: false, duplicate: true };
      }
      throw error;
    }
  }

  private async validateEndpointInput(input: RemoteMcpEndpointInput): Promise<{
    url: string;
    authHeaderName?: string;
    allowedHosts: string[];
    tools: RemoteMcpToolPolicy[];
  }> {
    if (input.transport === "sse") {
      throw new BadRequestException(
        "Remote MCP legacy SSE transport is not enabled in the Voice runtime",
      );
    }
    if (input.secret && /[\u0000-\u001f\u007f]/u.test(input.secret)) {
      throw new BadRequestException("Remote MCP secret contains unsupported control characters");
    }
    if (input.authType === "none" && (input.secret || input.authHeaderName)) {
      throw new BadRequestException("Remote MCP auth fields require bearer or header auth");
    }
    if (input.authType !== "none" && !input.secret) {
      throw new BadRequestException("Remote MCP authenticated endpoint requires a secret");
    }
    let authHeaderName: string | undefined;
    if (input.authType === "header") {
      const value = input.authHeaderName?.trim();
      if (!value || !/^[A-Za-z][A-Za-z0-9-]{0,63}$/.test(value)) {
        throw new BadRequestException("Remote MCP authHeaderName is invalid");
      }
      const lower = value.toLowerCase();
      if (forbiddenHeaderNames.has(lower) || lower.startsWith("x-forwarded-")) {
        throw new BadRequestException("Remote MCP authHeaderName is not allowed");
      }
      authHeaderName = value;
    } else if (input.authHeaderName) {
      throw new BadRequestException("Remote MCP authHeaderName requires header auth");
    }
    const tools = this.validateTools(input.tools);
    const normalizedUrl = await this.assertSafeNetwork(
      input.url,
      input.networkPolicy,
      input.allowedHosts,
    );
    const hostname = canonicalHostname(new URL(normalizedUrl).hostname);
    return {
      url: normalizedUrl,
      ...(authHeaderName ? { authHeaderName } : {}),
      allowedHosts: [hostname],
      tools,
    };
  }

  private validateTools(tools: readonly RemoteMcpToolPolicy[]): RemoteMcpToolPolicy[] {
    if (!tools.length || tools.length > 128) {
      throw new BadRequestException("Remote MCP endpoint must allow 1 to 128 tools");
    }
    const names = new Set<string>();
    return tools.map((tool) => {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(tool.name)) {
        throw new BadRequestException(`Remote MCP tool name ${tool.name} is invalid`);
      }
      if (names.has(tool.name)) {
        throw new BadRequestException(`Remote MCP tool ${tool.name} is duplicated`);
      }
      names.add(tool.name);
      if (
        (tool.safetyClass === "disruptive" || tool.safetyClass === "destructive") &&
        !tool.requiresConfirmation
      ) {
        throw new BadRequestException(
          `Remote MCP tool ${tool.name} requires confirmation for its safety class`,
        );
      }
      return {
        name: tool.name,
        safetyClass: tool.safetyClass,
        requiresConfirmation: tool.requiresConfirmation,
      };
    });
  }

  private async assertSafeNetwork(
    rawUrl: string,
    networkPolicy: RemoteMcpNetworkPolicyValue,
    allowedHosts: readonly string[],
  ): Promise<string> {
    try {
      return (await this.resolveSafeNetwork(rawUrl, networkPolicy, allowedHosts)).url;
    } catch (error) {
      if (error instanceof Error && error.message === "probe_dns_timeout") {
        throw new BadRequestException("Remote MCP hostname resolution timed out");
      }
      throw error;
    }
  }

  private async resolveSafeNetwork(
    rawUrl: string,
    networkPolicy: RemoteMcpNetworkPolicyValue,
    allowedHosts: readonly string[],
    deadlineAt = Date.now() + 5_000,
  ): Promise<{
    url: string;
    hostname: string;
    addresses: string[];
    networkPolicy: RemoteMcpNetworkPolicyValue;
  }> {
    let parsed: URL;
    try {
      parsed = new URL(rawUrl);
    } catch {
      throw new BadRequestException("Remote MCP URL is invalid");
    }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new BadRequestException("Remote MCP URL must use HTTP or HTTPS");
    }
    if (networkPolicy === "public_only" && parsed.protocol !== "https:") {
      throw new BadRequestException("Public Remote MCP endpoints must use HTTPS");
    }
    if (parsed.username || parsed.password || parsed.hash || parsed.search) {
      throw new BadRequestException(
        "Remote MCP URL cannot contain credentials, fragments or query parameters",
      );
    }
    const hostname = canonicalHostname(parsed.hostname);
    if (
      !hostname ||
      hostname === "localhost" ||
      hostname.endsWith(".localhost") ||
      metadataHostnames.has(hostname)
    ) {
      throw new BadRequestException("Remote MCP hostname is blocked");
    }
    const normalizedAllowedHosts = allowedHosts.map(canonicalHostname);
    if (
      normalizedAllowedHosts.length !== 1 ||
      normalizedAllowedHosts[0] !== hostname ||
      normalizedAllowedHosts.some((host) => !host || host.includes("*") || host.includes("/"))
    ) {
      throw new BadRequestException(
        "Remote MCP allowedHosts must contain only the exact endpoint hostname",
      );
    }
    let addresses: string[];
    try {
      addresses = isIP(hostname)
        ? [hostname]
        : await this.lookupAddresses(hostname, deadlineAt);
    } catch (error) {
      if (error instanceof Error && error.message === "probe_dns_timeout") throw error;
      throw new BadRequestException("Remote MCP hostname could not be resolved");
    }
    if (!addresses.length) throw new BadRequestException("Remote MCP hostname has no address");
    for (const address of addresses) {
      const classification = this.addressClassification(address);
      if (classification === "forbidden") {
        throw new BadRequestException("Remote MCP endpoint resolves to a forbidden address");
      }
      if (classification === "private" && networkPolicy !== "private_allowlist") {
        throw new BadRequestException(
          "Remote MCP private network access requires private_allowlist policy",
        );
      }
    }
    parsed.hostname = hostname;
    return { url: parsed.toString(), hostname, addresses, networkPolicy };
  }

  private lookupAddresses(hostname: string, deadlineAt: number): Promise<string[]> {
    const remainingMs = deadlineAt - Date.now();
    if (remainingMs <= 0) return Promise.reject(new Error("probe_dns_timeout"));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("probe_dns_timeout")), remainingMs);
      void lookup(hostname, { all: true, verbatim: true }).then(
        (values) => {
          clearTimeout(timer);
          resolve(values.map(({ address }) => address));
        },
        (error: unknown) => {
          clearTimeout(timer);
          reject(error);
        },
      );
    });
  }

  private addressClassification(address: string): "public" | "private" | "forbidden" {
    const family = isIP(address);
    if (family === 4) {
      if (forbiddenIpv4.check(address, "ipv4")) return "forbidden";
      if (privateIpv4.check(address, "ipv4")) return "private";
      return "public";
    }
    if (family !== 6) return "forbidden";
    if (forbiddenIpv6.check(address, "ipv6")) return "forbidden";
    if (privateIpv6.check(address, "ipv6")) return "private";
    return "public";
  }

  private async probe(endpoint: {
    url: string;
    transport: RemoteMcpTransport;
    authType: RemoteMcpAuthType;
    authHeaderName: string | null;
    secretCiphertext: string | null;
    secretConfigured: boolean;
    timeoutMs: number;
    resultMaxBytes: number;
    tools: Prisma.JsonValue;
  }, target: {
    url: string;
    hostname: string;
    addresses: string[];
    networkPolicy: RemoteMcpNetworkPolicyValue;
  }, deadlineAt: number): Promise<void> {
    if (endpoint.transport !== RemoteMcpTransport.STREAMABLE_HTTP) {
      throw new Error("probe_transport_unsupported");
    }
    const addresses = [...target.addresses].sort((left, right) => isIP(left) - isIP(right));
    let lastError: unknown = new Error("probe_dns_empty");
    for (const [index, address] of addresses.entries()) {
      if (Date.now() >= deadlineAt) throw new Error("probe_timeout");
      const remainingAddresses = addresses.length - index;
      const addressDeadline = remainingAddresses === 1
        ? deadlineAt
        : Math.min(
            deadlineAt,
            Date.now() + Math.max(1_000, Math.floor((deadlineAt - Date.now()) / remainingAddresses)),
          );
      try {
        await this.probePinned(endpoint, { ...target, addresses: [address] }, addressDeadline);
        return;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError;
  }

  private async probePinned(endpoint: {
    authType: RemoteMcpAuthType;
    authHeaderName: string | null;
    secretCiphertext: string | null;
    secretConfigured: boolean;
    resultMaxBytes: number;
    tools: Prisma.JsonValue;
  }, target: {
    url: string;
    hostname: string;
    addresses: string[];
    networkPolicy: RemoteMcpNetworkPolicyValue;
  }, deadlineAt: number): Promise<void> {
    const initialize = await this.probeRequest(
      endpoint,
      target,
      {
        jsonrpc: "2.0",
        id: "veetee-manager-health-initialize",
        method: "initialize",
        params: {
          protocolVersion: "2025-03-26",
          capabilities: {},
          clientInfo: { name: "veetee-manager", version: "1" },
        },
      },
      deadlineAt,
    );
    const initialized = this.parseProbeResult(
      initialize.body,
      initialize.contentType,
      "veetee-manager-health-initialize",
    );
    if (
      typeof initialized.protocolVersion !== "string" ||
      !["2024-11-05", "2025-03-26", "2025-06-18"].includes(
        initialized.protocolVersion,
      )
    ) {
      throw new Error("probe_initialize_invalid");
    }
    await this.probeRequest(
      endpoint,
      target,
      { jsonrpc: "2.0", method: "notifications/initialized" },
      deadlineAt,
      initialize.sessionId,
      true,
    );
    const discovered = new Set<string>();
    const cursors = new Set<string>();
    let cursor: string | undefined;
    for (let page = 0; page < 32; page += 1) {
      const requestId = `veetee-manager-health-tools-${page}`;
      const tools = await this.probeRequest(
        endpoint,
        target,
        {
          jsonrpc: "2.0",
          id: requestId,
          method: "tools/list",
          params: cursor ? { cursor } : {},
        },
        deadlineAt,
        initialize.sessionId,
      );
      const toolsResult = this.parseProbeResult(
        tools.body,
        tools.contentType,
        requestId,
      );
      if (
        !Array.isArray(toolsResult.tools) ||
        toolsResult.tools.some((tool) => {
          if (!tool || typeof tool !== "object" || Array.isArray(tool)) return true;
          const name = (tool as Record<string, unknown>).name;
          return typeof name !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(name);
        })
      ) {
        throw new Error("probe_tools_invalid");
      }
      for (const tool of toolsResult.tools) {
        discovered.add((tool as Record<string, unknown>).name as string);
        if (discovered.size > 128) throw new Error("probe_tools_too_many");
      }
      const nextCursor = toolsResult.nextCursor;
      if (nextCursor === undefined || nextCursor === null || nextCursor === "") {
        cursor = undefined;
        break;
      }
      if (
        typeof nextCursor !== "string" ||
        nextCursor.length > 256 ||
        cursors.has(nextCursor)
      ) {
        throw new Error("probe_tools_cursor_invalid");
      }
      cursors.add(nextCursor);
      cursor = nextCursor;
      if (page === 31) throw new Error("probe_tools_pages_exceeded");
    }
    if (this.tools(endpoint.tools).some(({ name }) => !discovered.has(name))) {
      throw new Error("probe_tool_allowlist_missing");
    }
  }

  private probeRequest(
    endpoint: {
      authType: RemoteMcpAuthType;
      authHeaderName: string | null;
      secretCiphertext: string | null;
      secretConfigured: boolean;
      resultMaxBytes: number;
    },
    target: {
      url: string;
      hostname: string;
      addresses: string[];
      networkPolicy: RemoteMcpNetworkPolicyValue;
    },
    payload: Record<string, unknown>,
    deadlineAt: number,
    sessionId?: string,
    allowEmpty = false,
  ): Promise<{ body: string; contentType: string; sessionId?: string }> {
    const remainingMs = deadlineAt - Date.now();
    if (remainingMs <= 0) return Promise.reject(new Error("probe_timeout"));
    const address = target.addresses[0];
    if (!address) return Promise.reject(new Error("probe_dns_empty"));
    const parsed = new URL(target.url);
    const body = JSON.stringify(payload);
    const expectedResponseId =
      typeof payload.id === "string" || typeof payload.id === "number"
        ? payload.id
        : undefined;
    const maximumBytes = Math.min(endpoint.resultMaxBytes, 65_536);
    const headers: Record<string, string | number> = {
      Accept: "application/json, text/event-stream",
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(body),
      Host: parsed.host,
      "MCP-Protocol-Version": "2025-03-26",
      ...this.authHeaders(endpoint),
      ...(sessionId ? { "MCP-Session-Id": sessionId } : {}),
    };

    return new Promise((resolve, reject) => {
      let settled = false;
      let absoluteTimer: NodeJS.Timeout | undefined;
      const finishError = (error: Error) => {
        if (settled) return;
        settled = true;
        if (absoluteTimer) clearTimeout(absoluteTimer);
        reject(error);
      };
      const finishSuccess = (value: {
        body: string;
        contentType: string;
        sessionId?: string;
      }) => {
        if (settled) return;
        settled = true;
        if (absoluteTimer) clearTimeout(absoluteTimer);
        resolve(value);
      };
      const options = {
        hostname: address,
        port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
        path: parsed.pathname,
        method: "POST",
        headers,
        agent: false,
      };
      const onResponse = (response: import("node:http").IncomingMessage) => {
        const status = response.statusCode ?? 0;
        if (status < 200 || status >= 300) {
          response.resume();
          finishError(new Error("probe_http_error"));
          return;
        }
        const peer = this.canonicalPeerAddress(response.socket.remoteAddress);
        const pinned = this.canonicalPeerAddress(address);
        if (!peer || peer !== pinned) {
          response.destroy();
          finishError(new Error("probe_peer_mismatch"));
          return;
        }
        const classification = this.addressClassification(peer);
        if (
          classification === "forbidden" ||
          (classification === "private" && target.networkPolicy !== "private_allowlist")
        ) {
          response.destroy();
          finishError(new Error("probe_peer_blocked"));
          return;
        }
        const contentType = String(response.headers["content-type"] ?? "").toLowerCase();
        const contentLength = Number(response.headers["content-length"]);
        if (Number.isFinite(contentLength) && contentLength > maximumBytes) {
          response.destroy();
          finishError(new Error("probe_response_too_large"));
          return;
        }
        const chunks: Buffer[] = [];
        let received = 0;
        const completeResponse = (responseBody: string) => {
          if (!allowEmpty && !responseBody) {
            finishError(new Error("probe_empty_response"));
            return;
          }
          if (
            responseBody &&
            !contentType.includes("application/json") &&
            !contentType.includes("text/event-stream")
          ) {
            finishError(new Error("probe_content_type"));
            return;
          }
          const returnedSessionId = response.headers["mcp-session-id"];
          const normalizedSessionId = Array.isArray(returnedSessionId)
            ? returnedSessionId[0]
            : returnedSessionId;
          if (
            normalizedSessionId &&
            (normalizedSessionId.length > 256 || /[\u0000-\u001f\u007f]/u.test(normalizedSessionId))
          ) {
            finishError(new Error("probe_session_id_invalid"));
            return;
          }
          finishSuccess({
            body: responseBody,
            contentType,
            ...(normalizedSessionId ? { sessionId: normalizedSessionId } : {}),
          });
        };
        response.on("data", (chunk: Buffer | string) => {
          if (settled) return;
          const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          received += bytes.length;
          if (received > maximumBytes) {
            response.destroy();
            finishError(new Error("probe_response_too_large"));
            return;
          }
          chunks.push(bytes);
          if (contentType.includes("text/event-stream")) {
            const buffered = Buffer.concat(chunks).toString("utf8");
            if (
              expectedResponseId !== undefined &&
              this.findSseResponse(buffered, expectedResponseId, true)
            ) {
              completeResponse(buffered);
              response.destroy();
            }
          }
        });
        response.on("end", () => {
          if (!settled) completeResponse(Buffer.concat(chunks).toString("utf8"));
        });
        response.on("error", (error) => finishError(error));
      };
      const request = parsed.protocol === "https:"
        ? httpsRequest(
            { ...options, servername: target.hostname, rejectUnauthorized: true },
            onResponse,
          )
        : httpRequest(options, onResponse);
      absoluteTimer = setTimeout(() => {
        request.destroy();
        finishError(new Error("probe_timeout"));
      }, remainingMs);
      request.on("error", (error) => finishError(error));
      request.end(body);
    });
  }

  private parseProbeResult(
    body: string,
    contentType: string,
    expectedId: string,
  ): Record<string, unknown> {
    let encoded = body;
    if (contentType.includes("text/event-stream")) {
      const response = this.findSseResponse(body, expectedId, false);
      if (!response) throw new Error("probe_sse_invalid");
      encoded = response;
    }
    let payload: unknown;
    try {
      payload = JSON.parse(encoded);
    } catch {
      throw new Error("probe_json_invalid");
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("probe_jsonrpc_invalid");
    }
    const response = payload as Record<string, unknown>;
    if (
      response.jsonrpc !== "2.0" ||
      response.id !== expectedId ||
      response.error !== undefined ||
      !response.result ||
      typeof response.result !== "object" ||
      Array.isArray(response.result)
    ) {
      throw new Error("probe_jsonrpc_invalid");
    }
    return response.result as Record<string, unknown>;
  }

  private findSseResponse(
    body: string,
    expectedId: string | number,
    completeEventsOnly: boolean,
  ): string | undefined {
    const normalized = body.replace(/\r\n?/gu, "\n");
    const terminated = /\n\n$/u.test(normalized);
    const events = normalized.split(/\n{2,}/u);
    if (completeEventsOnly && !terminated) events.pop();
    for (const event of events) {
      const dataLines: string[] = [];
      for (const line of event.split("\n")) {
        if (line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator === -1 ? line : line.slice(0, separator);
        if (field !== "data") continue;
        let value = separator === -1 ? "" : line.slice(separator + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        dataLines.push(value);
      }
      if (!dataLines.length) continue;
      const encoded = dataLines.join("\n");
      if (!encoded || encoded === "[DONE]") continue;
      try {
        const payload = JSON.parse(encoded) as unknown;
        if (
          payload &&
          typeof payload === "object" &&
          !Array.isArray(payload) &&
          (payload as Record<string, unknown>).id === expectedId
        ) return encoded;
      } catch {
        // Keep looking: progress/keepalive events are not the requested JSON-RPC response.
      }
    }
    return undefined;
  }

  private canonicalPeerAddress(value: string | undefined): string | undefined {
    if (!value) return undefined;
    const normalized = value.toLowerCase();
    const mapped = normalized.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/u);
    return mapped?.[1] ?? normalized;
  }

  private authHeaders(endpoint: {
    authType: RemoteMcpAuthType;
    authHeaderName: string | null;
    secretCiphertext: string | null;
    secretConfigured: boolean;
  }): Record<string, string> {
    if (endpoint.authType === RemoteMcpAuthType.NONE) return {};
    if (!endpoint.secretConfigured || !endpoint.secretCiphertext) {
      throw new ConflictException("Remote MCP endpoint credential is not configured");
    }
    if (
      endpoint.authType === RemoteMcpAuthType.HEADER &&
      (
        !endpoint.authHeaderName ||
        !/^[A-Za-z][A-Za-z0-9-]{0,63}$/.test(endpoint.authHeaderName) ||
        forbiddenHeaderNames.has(endpoint.authHeaderName.toLowerCase()) ||
        endpoint.authHeaderName.toLowerCase().startsWith("x-forwarded-")
      )
    ) {
      throw new ConflictException("Remote MCP authentication header is not allowed");
    }
    const secret = this.secretCrypto.decrypt(endpoint.secretCiphertext);
    if (endpoint.authType === RemoteMcpAuthType.BEARER) {
      return { Authorization: `Bearer ${secret}` };
    }
    return { [endpoint.authHeaderName!]: secret };
  }

  private snapshotAssignments(value: unknown): Array<{
    endpointId: string;
    toolNames: string[];
    timeoutSeconds: number;
  }> {
    if (!Array.isArray(value)) return [];
    return value.slice(0, 32).flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const record = item as Record<string, unknown>;
      if (
        typeof record.endpointId !== "string" ||
        !Array.isArray(record.toolNames) ||
        record.toolNames.length === 0 ||
        record.toolNames.length > 128 ||
        record.toolNames.some((name) => typeof name !== "string") ||
        typeof record.timeoutSeconds !== "number" ||
        !Number.isFinite(record.timeoutSeconds) ||
        record.timeoutSeconds < 5 ||
        record.timeoutSeconds > 30
      ) return [];
      return [{
        endpointId: record.endpointId,
        toolNames: record.toolNames as string[],
        timeoutSeconds: record.timeoutSeconds,
      }];
    });
  }

  private tools(value: Prisma.JsonValue): RemoteMcpToolPolicy[] {
    if (!Array.isArray(value)) return [];
    return value.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const record = item as Record<string, unknown>;
      if (
        typeof record.name !== "string" ||
        !["read_only", "reversible", "disruptive", "destructive"].includes(
          String(record.safetyClass),
        ) ||
        typeof record.requiresConfirmation !== "boolean"
      ) return [];
      return [{
        name: record.name,
        safetyClass: record.safetyClass as RemoteMcpSafetyClass,
        requiresConfirmation: record.requiresConfirmation,
      }];
    });
  }

  private endpointRecord(endpoint: {
    id: string;
    name: string;
    url: string;
    transport: RemoteMcpTransport;
    enabled: boolean;
    authType: RemoteMcpAuthType;
    authHeaderName: string | null;
    secretConfigured: boolean;
    timeoutMs: number;
    resultMaxBytes: number;
    networkPolicy: RemoteMcpNetworkPolicy;
    allowedHosts: string[];
    tools: Prisma.JsonValue;
    health: RemoteMcpHealth;
    healthLatencyMs: number | null;
    healthErrorCode: string | null;
    healthCheckedAt: Date | null;
    createdAt: Date;
    updatedAt: Date;
  }): RemoteMcpEndpointRecord {
    return {
      id: endpoint.id,
      name: endpoint.name,
      url: endpoint.url,
      transport: this.transportValue(endpoint.transport),
      enabled: endpoint.enabled,
      authType: this.authTypeValue(endpoint.authType),
      ...(endpoint.authHeaderName ? { authHeaderName: endpoint.authHeaderName } : {}),
      secretConfigured: endpoint.secretConfigured,
      timeoutSeconds: endpoint.timeoutMs / 1_000,
      resultMaxBytes: endpoint.resultMaxBytes,
      networkPolicy: this.networkPolicyValue(endpoint.networkPolicy),
      allowedHosts: endpoint.allowedHosts,
      tools: this.tools(endpoint.tools),
      health: this.healthValue(endpoint.health),
      ...(endpoint.healthLatencyMs !== null ? { healthLatencyMs: endpoint.healthLatencyMs } : {}),
      ...(endpoint.healthErrorCode ? { healthErrorCode: endpoint.healthErrorCode } : {}),
      ...(endpoint.healthCheckedAt
        ? { healthCheckedAt: endpoint.healthCheckedAt.toISOString() }
        : {}),
      createdAt: endpoint.createdAt.toISOString(),
      updatedAt: endpoint.updatedAt.toISOString(),
    };
  }

  private endpointAuditShape(record: RemoteMcpEndpointRecord): Record<string, unknown> {
    return {
      ...record,
      tools: record.tools.map(({ name, safetyClass, requiresConfirmation }) => ({
        name,
        safetyClass,
        requiresConfirmation,
      })),
    };
  }

  private assignmentRecord(assignment: {
    endpointId: string;
    toolNames: string[];
    timeoutMs: number;
    endpoint: {
      name: string;
      enabled: boolean;
      health: RemoteMcpHealth;
    };
  }): RemoteMcpAssignmentRecord {
    return {
      endpointId: assignment.endpointId,
      endpointName: assignment.endpoint.name,
      enabled: assignment.endpoint.enabled,
      toolNames: assignment.toolNames,
      timeoutSeconds: assignment.timeoutMs / 1_000,
      health: this.healthValue(assignment.endpoint.health),
    };
  }

  private async requireAgent(tenantId: string, agentId: string): Promise<void> {
    const agent = await this.prisma.agent.findFirst({
      where: { id: agentId, tenantId },
      select: { id: true },
    });
    if (!agent) throw new NotFoundException("Agent not found");
  }

  private async auditRemoteMcpResolve(
    tenantId: string,
    agentId: string,
    deviceId: string,
    configVersion: number,
    endpointIds: string[],
  ): Promise<void> {
    await this.audit.record({
      tenantId,
      action: "remote_mcp.resolve",
      targetType: "agent",
      targetId: agentId,
      requestId: `remote-mcp-resolve:${agentId}:${deviceId}:${Date.now()}`,
      details: { deviceId, configVersion, endpointIds, endpointCount: endpointIds.length },
    });
  }

  private transportValue(value: RemoteMcpTransport): RemoteMcpTransportValue {
    return value === RemoteMcpTransport.STREAMABLE_HTTP ? "streamable_http" : "sse";
  }

  private authTypeValue(value: RemoteMcpAuthType): RemoteMcpAuthTypeValue {
    if (value === RemoteMcpAuthType.BEARER) return "bearer";
    if (value === RemoteMcpAuthType.HEADER) return "header";
    return "none";
  }

  private networkPolicyValue(value: RemoteMcpNetworkPolicy): RemoteMcpNetworkPolicyValue {
    return value === RemoteMcpNetworkPolicy.PRIVATE_ALLOWLIST
      ? "private_allowlist"
      : "public_only";
  }

  private healthValue(value: RemoteMcpHealth): RemoteMcpHealthValue {
    if (value === RemoteMcpHealth.HEALTHY) return "healthy";
    if (value === RemoteMcpHealth.DEGRADED) return "degraded";
    return "unknown";
  }

  private probeErrorCode(error: unknown): string {
    if (error instanceof DOMException && error.name === "AbortError") return "probe_timeout";
    if (error instanceof BadRequestException) return "probe_ssrf_policy";
    if (error instanceof ConflictException) return "probe_credential_unavailable";
    if (error instanceof Error && /^probe_[a-z_]+$/.test(error.message)) return error.message;
    return "probe_network_error";
  }
}
