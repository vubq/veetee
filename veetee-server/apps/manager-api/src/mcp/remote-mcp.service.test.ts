import { BadRequestException, ConflictException } from "@nestjs/common";
import {
  RemoteMcpAuthType,
  RemoteMcpHealth,
  RemoteMcpNetworkPolicy,
  RemoteMcpTransport,
  TenantRole,
} from "@prisma/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuditService } from "../audit/audit.service.js";
import type { Principal } from "../auth/auth.types.js";
import type { PrismaService } from "../database/prisma.service.js";
import { SecretCryptoService } from "../security/secret-crypto.service.js";
import { RemoteMcpService } from "./remote-mcp.service.js";

const principal: Principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "tenant",
  role: TenantRole.ADMIN,
  email: "admin@example.test",
  displayName: "Admin",
};

const tool = {
  name: "weather.current",
  safetyClass: "read_only" as const,
  requiresConfirmation: false,
};

describe("RemoteMcpService", () => {
  beforeEach(() => {
    process.env.VEETEE_MASTER_KEY = Buffer.alloc(32, 11).toString("base64");
  });

  it("blocks loopback and link-local targets even with private LAN policy", async () => {
    const service = new RemoteMcpService(
      {} as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    await expect(service.createEndpoint({
      name: "Loopback",
      url: "http://127.0.0.1:3000/mcp",
      transport: "streamable_http",
      authType: "none",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "private_allowlist",
      allowedHosts: ["127.0.0.1"],
      tools: [tool],
    }, { principal, requestId: "request-1" })).rejects.toBeInstanceOf(BadRequestException);
    await expect(service.createEndpoint({
      name: "Metadata",
      url: "http://169.254.169.254/mcp",
      transport: "streamable_http",
      authType: "none",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "private_allowlist",
      allowedHosts: ["169.254.169.254"],
      tools: [tool],
    }, { principal, requestId: "request-2" })).rejects.toBeInstanceOf(BadRequestException);
  });

  it("classifies IPv4-compatible and transition IPv6 ranges as forbidden", () => {
    const service = new RemoteMcpService(
      {} as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    const classify = (address: string) => (
      service as unknown as {
        addressClassification(value: string): "public" | "private" | "forbidden";
      }
    ).addressClassification(address);
    expect([
      "::127.0.0.1",
      "::ffff:127.0.0.1",
      "64:ff9b::7f00:1",
      "64:ff9b:1::7f00:1",
      "2001::1",
      "2002:7f00:1::",
    ].map(classify)).toEqual(Array.from({ length: 6 }, () => "forbidden"));
    expect(classify("fd12:3456::1")).toBe("private");
    expect(classify("2001:4860:4860::8888")).toBe("public");
  });

  it("normalizes and classifies IPv6 literal endpoint URLs without DNS lookup", async () => {
    const service = new RemoteMcpService(
      {} as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    const validate = (input: Parameters<RemoteMcpService["createEndpoint"]>[0]) => (
      service as unknown as {
        validateEndpointInput(value: typeof input): Promise<{
          url: string;
          allowedHosts: string[];
        }>;
      }
    ).validateEndpointInput(input);
    const base = {
      name: "IPv6 MCP",
      transport: "streamable_http" as const,
      authType: "none" as const,
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      tools: [tool],
    };
    await expect(validate({
      ...base,
      url: "http://[fd12:3456::1]/mcp",
      networkPolicy: "private_allowlist",
      allowedHosts: ["[fd12:3456::1]"],
    })).resolves.toMatchObject({
      url: "http://[fd12:3456::1]/mcp",
      allowedHosts: ["fd12:3456::1"],
    });
    await expect(validate({
      ...base,
      url: "https://[2606:4700:4700::1111]/mcp",
      networkPolicy: "public_only",
      allowedHosts: ["2606:4700:4700::1111"],
    })).resolves.toMatchObject({
      url: "https://[2606:4700:4700::1111]/mcp",
      allowedHosts: ["2606:4700:4700::1111"],
    });
    await expect(validate({
      ...base,
      url: "http://[::1]/mcp",
      networkPolicy: "private_allowlist",
      allowedHosts: ["::1"],
    })).rejects.toBeInstanceOf(BadRequestException);
  });

  it("requires explicit private allowlist and exact host", async () => {
    const service = new RemoteMcpService(
      {} as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    await expect(service.createEndpoint({
      name: "Home Assistant",
      url: "https://192.168.1.10/mcp",
      transport: "streamable_http",
      authType: "none",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "public_only",
      allowedHosts: ["192.168.1.10"],
      tools: [tool],
    }, { principal, requestId: "request-1" })).rejects.toBeInstanceOf(BadRequestException);
    await expect(service.createEndpoint({
      name: "Wrong host",
      url: "http://192.168.1.10/mcp",
      transport: "streamable_http",
      authType: "none",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "private_allowlist",
      allowedHosts: ["192.168.1.11"],
      tools: [tool],
    }, { principal, requestId: "request-2" })).rejects.toBeInstanceOf(BadRequestException);
  });

  it("fails closed for legacy SSE until the Voice runtime has a conforming adapter", async () => {
    const service = new RemoteMcpService(
      {} as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    await expect(service.createEndpoint({
      name: "Legacy SSE",
      url: "https://93.184.216.34/sse",
      transport: "sse",
      authType: "none",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "public_only",
      allowedHosts: ["93.184.216.34"],
      tools: [tool],
    }, { principal, requestId: "request-sse" })).rejects.toThrow(/not enabled/i);
  });

  it.each([
    "Accept",
    "Content-Type",
    "MCP-Protocol-Version",
    "MCP-Session-Id",
    "Forwarded",
    "TE",
    "Trailer",
    "Connection",
    "Authorization",
    "X-Forwarded-For",
  ])("rejects reserved custom authentication header %s before persistence", async (header) => {
    const prisma = { $transaction: vi.fn() };
    const service = new RemoteMcpService(
      prisma as unknown as PrismaService,
      {} as AuditService,
      new SecretCryptoService(),
    );
    await expect(service.createEndpoint({
      name: `Reserved ${header}`,
      url: "https://93.184.216.34/mcp",
      transport: "streamable_http",
      authType: "header",
      authHeaderName: header,
      secret: "reserved-header-secret",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "public_only",
      allowedHosts: ["93.184.216.34"],
      tools: [tool],
    }, { principal, requestId: `request-${header}` })).rejects.toBeInstanceOf(
      BadRequestException,
    );
    expect(prisma.$transaction).not.toHaveBeenCalled();
  });

  it("encrypts endpoint credentials and never returns or audits the raw secret", async () => {
    const audit = { record: vi.fn().mockResolvedValue(undefined) };
    const transaction = {
      remoteMcpEndpoint: {
        create: vi.fn(async ({ data }) => ({
          id: "endpoint-1",
          name: data.name,
          url: data.url,
          transport: data.transport,
          enabled: true,
          authType: data.authType,
          authHeaderName: data.authHeaderName ?? null,
          secretCiphertext: data.secretCiphertext ?? null,
          secretConfigured: data.secretConfigured ?? false,
          timeoutMs: data.timeoutMs,
          resultMaxBytes: data.resultMaxBytes,
          networkPolicy: data.networkPolicy,
          allowedHosts: data.allowedHosts,
          tools: data.tools,
          health: RemoteMcpHealth.UNKNOWN,
          healthLatencyMs: null,
          healthErrorCode: null,
          healthCheckedAt: null,
          createdAt: new Date("2026-07-29T04:00:00.000Z"),
          updatedAt: new Date("2026-07-29T04:00:00.000Z"),
        })),
      },
    };
    const prisma = {
      $transaction: vi.fn(async (operation: (client: typeof transaction) => unknown) =>
        operation(transaction)),
    };
    const service = new RemoteMcpService(
      prisma as unknown as PrismaService,
      audit as unknown as AuditService,
      new SecretCryptoService(),
    );
    const record = await service.createEndpoint({
      name: "Weather",
      url: "https://93.184.216.34/mcp",
      transport: "streamable_http",
      authType: "bearer",
      secret: "remote-mcp-test-secret",
      timeoutSeconds: 10,
      resultMaxBytes: 16_384,
      networkPolicy: "public_only",
      allowedHosts: ["93.184.216.34"],
      tools: [tool],
    }, { principal, requestId: "request-1" });
    expect(record).not.toHaveProperty("secret");
    expect(JSON.stringify(record)).not.toContain("remote-mcp-test-secret");
    const stored = transaction.remoteMcpEndpoint.create.mock.calls[0]![0].data;
    expect(stored.secretCiphertext).not.toContain("remote-mcp-test-secret");
    expect(JSON.stringify(audit.record.mock.calls)).not.toContain("remote-mcp-test-secret");
  });

  it("resolves only tools frozen into the agent version and returns auth internally", async () => {
    const crypto = new SecretCryptoService();
    const endpoint = {
      id: "endpoint-1",
      tenantId: "tenant-1",
      name: "Weather",
      url: "https://93.184.216.34/mcp",
      transport: RemoteMcpTransport.STREAMABLE_HTTP,
      enabled: true,
      authType: RemoteMcpAuthType.BEARER,
      authHeaderName: null,
      secretCiphertext: crypto.encrypt("internal-only-secret"),
      secretConfigured: true,
      timeoutMs: 20_000,
      resultMaxBytes: 16_384,
      networkPolicy: RemoteMcpNetworkPolicy.PUBLIC_ONLY,
      allowedHosts: ["93.184.216.34"],
      tools: [tool, {
        name: "weather.admin.reset",
        safetyClass: "destructive",
        requiresConfirmation: true,
      }],
      health: RemoteMcpHealth.HEALTHY,
      healthLatencyMs: 50,
      healthErrorCode: null,
      healthCheckedAt: new Date(),
      createdAt: new Date(),
      updatedAt: new Date(),
    };
    const prisma = {
      agentConfigVersion: { findUnique: vi.fn().mockResolvedValue({
        agent: { tenantId: "tenant-1" },
        snapshot: {
          remoteMcpEndpoints: [{
            endpointId: "endpoint-1",
            toolNames: ["weather.current"],
            timeoutSeconds: 10,
          }],
        },
      }) },
      device: { findFirst: vi.fn().mockResolvedValue({ id: "device-1" }) },
      remoteMcpEndpoint: { findMany: vi.fn().mockResolvedValue([endpoint]) },
    };
    const service = new RemoteMcpService(
      prisma as unknown as PrismaService,
      { record: vi.fn().mockResolvedValue(undefined) } as unknown as AuditService,
      crypto,
    );
    const result = await service.resolve("agent-1", "device-1", 4);
    expect(result.endpoints).toEqual([
      expect.objectContaining({
        id: "endpoint-1",
        headers: { Authorization: "Bearer internal-only-secret" },
        timeoutSeconds: 10,
        allowedTools: [tool],
      }),
    ]);
  });

  it("fails closed for a legacy stored endpoint with a reserved auth header", async () => {
    const crypto = new SecretCryptoService();
    const endpoint = {
      id: "endpoint-legacy-header",
      tenantId: "tenant-1",
      name: "Legacy invalid header",
      url: "https://93.184.216.34/mcp",
      transport: RemoteMcpTransport.STREAMABLE_HTTP,
      enabled: true,
      authType: RemoteMcpAuthType.HEADER,
      authHeaderName: "Content-Type",
      secretCiphertext: crypto.encrypt("legacy-secret"),
      secretConfigured: true,
      timeoutMs: 10_000,
      resultMaxBytes: 16_384,
      networkPolicy: RemoteMcpNetworkPolicy.PUBLIC_ONLY,
      allowedHosts: ["93.184.216.34"],
      tools: [tool],
    };
    const prisma = {
      agentConfigVersion: { findUnique: vi.fn().mockResolvedValue({
        agent: { tenantId: "tenant-1" },
        snapshot: {
          remoteMcpEndpoints: [{
            endpointId: endpoint.id,
            toolNames: [tool.name],
            timeoutSeconds: 10,
          }],
        },
      }) },
      device: { findFirst: vi.fn().mockResolvedValue({ id: "device-1" }) },
      remoteMcpEndpoint: { findMany: vi.fn().mockResolvedValue([endpoint]) },
    };
    const service = new RemoteMcpService(
      prisma as unknown as PrismaService,
      { record: vi.fn().mockResolvedValue(undefined) } as unknown as AuditService,
      crypto,
    );
    await expect(service.resolve("agent-1", "device-1", 4)).rejects.toBeInstanceOf(
      ConflictException,
    );
  });
});
