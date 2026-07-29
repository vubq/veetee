import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";

import { RemoteMcpAuthType, RemoteMcpHealth, RemoteMcpNetworkPolicy, RemoteMcpTransport, TenantRole } from "@prisma/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

const probe = vi.hoisted(() => ({
  mode: "valid" as
    | "valid"
    | "invalid_jsonrpc"
    | "oversize"
    | "peer_mismatch"
    | "trickle"
    | "paginated"
    | "cursor_cycle"
    | "too_many_tools"
    | "too_many_pages"
    | "sse_keepalive"
    | "sse_wrong_id",
  requests: [] as Array<{ options: Record<string, unknown>; payload: Record<string, unknown> }>,
  interval: undefined as NodeJS.Timeout | undefined,
}));

vi.mock("node:https", () => ({
  request: (
    options: Record<string, unknown>,
    onResponse: (response: PassThrough) => void,
  ) => {
    const request = new EventEmitter() as EventEmitter & {
      end: (body: string) => void;
      destroy: () => void;
    };
    let response: PassThrough | undefined;
    request.destroy = () => {
      if (probe.interval) clearInterval(probe.interval);
      probe.interval = undefined;
      response?.destroy();
    };
    request.end = (body: string) => {
      const payload = JSON.parse(body) as Record<string, unknown>;
      probe.requests.push({ options, payload });
      if (probe.mode === "trickle") {
        response = fakeResponse("93.184.216.34", { "content-type": "application/json" });
        queueMicrotask(() => {
          onResponse(response!);
          probe.interval = setInterval(() => response?.write(" "), 3);
        });
        return;
      }
      const peer = probe.mode === "peer_mismatch" ? "127.0.0.1" : "93.184.216.34";
      const method = payload.method;
      const headers: Record<string, string> = { "content-type": "application/json" };
      let encoded = "";
      if (method === "initialize") {
        headers["mcp-session-id"] = "manager-health-session";
        encoded = probe.mode === "invalid_jsonrpc"
          ? "{}"
          : JSON.stringify({
              jsonrpc: "2.0",
              id: payload.id,
              result: { protocolVersion: "2025-03-26", capabilities: {} },
            });
        if (probe.mode === "sse_keepalive" || probe.mode === "sse_wrong_id") {
          headers["content-type"] = "text/event-stream";
          response = fakeResponse(peer, headers);
          queueMicrotask(() => {
            onResponse(response!);
            response!.write(": server keepalive\n\n");
            response!.write(
              `data: ${JSON.stringify({
                jsonrpc: "2.0",
                id: "unrelated-notification",
                result: {},
              })}\n\n`,
            );
            if (probe.mode === "sse_keepalive") {
              response!.write(`data: ${encoded}\n\n`);
            } else {
              response!.end();
            }
          });
          return;
        }
      } else if (method === "notifications/initialized") {
        encoded = "";
      } else {
        const params = payload.params as Record<string, unknown>;
        const cursor = params.cursor;
        let tools = [{ name: "weather.current", inputSchema: { type: "object" } }];
        let nextCursor: string | undefined;
        if (probe.mode === "paginated" && cursor === undefined) {
          tools = [{ name: "calendar.next", inputSchema: { type: "object" } }];
          nextCursor = "page-2";
        } else if (probe.mode === "cursor_cycle") {
          nextCursor = "same-cursor";
        } else if (probe.mode === "too_many_tools") {
          tools = Array.from({ length: 129 }, (_, index) => ({
            name: `tool.${index}`,
            inputSchema: { type: "object" },
          }));
        } else if (probe.mode === "too_many_pages") {
          nextCursor = `page-${probe.requests.length}`;
        }
        encoded = JSON.stringify({
          jsonrpc: "2.0",
          id: payload.id,
          result: { tools, ...(nextCursor ? { nextCursor } : {}) },
        });
      }
      if (probe.mode === "oversize") headers["content-length"] = "70000";
      response = fakeResponse(peer, headers, method === "notifications/initialized" ? 202 : 200);
      queueMicrotask(() => {
        onResponse(response!);
        response!.end(encoded);
      });
    };
    return request;
  },
}));

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

function fakeResponse(
  remoteAddress: string,
  headers: Record<string, string>,
  statusCode = 200,
): PassThrough {
  const response = new PassThrough();
  Object.defineProperties(response, {
    statusCode: { value: statusCode },
    headers: { value: headers },
    socket: { value: { remoteAddress } },
  });
  return response;
}

function harness(
  timeoutMs = 250,
  customAuth?: { headerName: string; secretCiphertext: string },
) {
  const endpoint = {
    id: "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
    tenantId: "tenant-1",
    name: "Weather",
    url: "https://93.184.216.34/mcp",
    transport: RemoteMcpTransport.STREAMABLE_HTTP,
    enabled: true,
    authType: customAuth ? RemoteMcpAuthType.HEADER : RemoteMcpAuthType.NONE,
    authHeaderName: customAuth?.headerName ?? null,
    secretCiphertext: customAuth?.secretCiphertext ?? null,
    secretConfigured: Boolean(customAuth),
    timeoutMs,
    resultMaxBytes: 65_536,
    networkPolicy: RemoteMcpNetworkPolicy.PUBLIC_ONLY,
    allowedHosts: ["93.184.216.34"],
    tools: [{
      name: "weather.current",
      safetyClass: "read_only",
      requiresConfirmation: false,
    }],
    health: RemoteMcpHealth.UNKNOWN,
    healthLatencyMs: null,
    healthErrorCode: null,
    healthCheckedAt: null,
    createdAt: new Date("2026-07-29T04:00:00.000Z"),
    updatedAt: new Date("2026-07-29T04:00:00.000Z"),
  };
  const transaction = {
    remoteMcpEndpoint: {
      update: vi.fn(async ({ data }) => ({
        ...endpoint,
        ...data,
        updatedAt: new Date("2026-07-29T04:01:00.000Z"),
      })),
    },
  };
  const prisma = {
    remoteMcpEndpoint: { findFirst: vi.fn().mockResolvedValue(endpoint) },
    $transaction: vi.fn(async (operation: (client: typeof transaction) => unknown) =>
      operation(transaction)),
  };
  const audit = { record: vi.fn().mockResolvedValue(undefined) };
  return {
    audit,
    service: new RemoteMcpService(
      prisma as unknown as PrismaService,
      audit as unknown as AuditService,
      new SecretCryptoService(),
    ),
  };
}

describe("Remote MCP pinned health probe", () => {
  beforeEach(() => {
    probe.mode = "valid";
    probe.requests = [];
    if (probe.interval) clearInterval(probe.interval);
    probe.interval = undefined;
  });

  it("pins the validated peer and verifies initialize plus tools/list", async () => {
    const { service } = harness();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: "request-valid" },
    );
    expect(result.health).toBe("healthy");
    expect(probe.requests.map(({ payload }) => payload.method)).toEqual([
      "initialize",
      "notifications/initialized",
      "tools/list",
    ]);
    expect(probe.requests[2]?.options).toMatchObject({
      hostname: "93.184.216.34",
      headers: expect.objectContaining({ "MCP-Session-Id": "manager-health-session" }),
    });
  });

  it("follows tools/list pagination before validating the configured allowlist", async () => {
    probe.mode = "paginated";
    const { service } = harness();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: "request-paginated" },
    );
    expect(result.health).toBe("healthy");
    expect(probe.requests.filter(({ payload }) => payload.method === "tools/list")).toHaveLength(2);
    expect(probe.requests.at(-1)?.payload.params).toEqual({ cursor: "page-2" });
  });

  it("ignores SSE comments and unrelated events until the expected response id arrives", async () => {
    probe.mode = "sse_keepalive";
    const { service } = harness();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: "request-sse-keepalive" },
    );
    expect(result.health).toBe("healthy");
  });

  it.each([
    ["invalid_jsonrpc", "probe_jsonrpc_invalid"],
    ["oversize", "probe_response_too_large"],
    ["peer_mismatch", "probe_peer_mismatch"],
  ] as const)("degrades bounded failure %s", async (mode, errorCode) => {
    probe.mode = mode;
    const { service } = harness();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: `request-${mode}` },
    );
    expect(result).toMatchObject({ health: "degraded", healthErrorCode: errorCode });
  });

  it.each([
    ["cursor_cycle", "probe_tools_cursor_invalid"],
    ["too_many_tools", "probe_tools_too_many"],
    ["too_many_pages", "probe_tools_pages_exceeded"],
    ["sse_wrong_id", "probe_sse_invalid"],
  ] as const)("fails closed for bounded protocol edge %s", async (mode, errorCode) => {
    probe.mode = mode;
    const { service } = harness();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: `request-${mode}` },
    );
    expect(result).toMatchObject({ health: "degraded", healthErrorCode: errorCode });
  });

  it("uses an absolute deadline even when the peer trickles bytes", async () => {
    probe.mode = "trickle";
    const { service } = harness(30);
    const started = Date.now();
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: "request-trickle" },
    );
    expect(result).toMatchObject({ health: "degraded", healthErrorCode: "probe_timeout" });
    expect(Date.now() - started).toBeLessThan(500);
  });

  it("fails a legacy reserved auth header before opening the probe connection", async () => {
    process.env.VEETEE_MASTER_KEY = Buffer.alloc(32, 13).toString("base64");
    const ciphertext = new SecretCryptoService().encrypt("legacy-probe-secret");
    const { service } = harness(250, {
      headerName: "MCP-Protocol-Version",
      secretCiphertext: ciphertext,
    });
    const result = await service.testEndpoint(
      "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      { principal, requestId: "request-reserved-header" },
    );
    expect(result).toMatchObject({
      health: "degraded",
      healthErrorCode: "probe_credential_unavailable",
    });
    expect(probe.requests).toEqual([]);
  });
});
