import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { TenantRole } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import type { RemoteMcpService } from "../mcp/remote-mcp.service.js";
import {
  AgentRemoteMcpController,
  CreateRemoteMcpEndpointDto,
  InternalRemoteMcpController,
  RemoteMcpAssignmentDto,
  RemoteMcpController,
} from "./remote-mcp.controller.js";

const principal: Principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "tenant",
  role: TenantRole.ADMIN,
  email: "admin@example.test",
  displayName: "Admin",
};
const request = { id: "request-1", headers: {} } as RequestWithPrincipal;

const endpoint = {
  name: "Weather",
  url: "https://mcp.example.test/v1",
  transport: "streamable_http",
  authType: "bearer",
  secret: "test-secret",
  timeoutSeconds: 10,
  resultMaxBytes: 16_384,
  networkPolicy: "public_only",
  allowedHosts: ["mcp.example.test"],
  tools: [{
    name: "weather.current",
    safetyClass: "read_only",
    requiresConfirmation: false,
  }],
};

describe("Remote MCP API contracts", () => {
  it("accepts only bounded explicit endpoint and assignment policies", async () => {
    await expect(validate(plainToInstance(CreateRemoteMcpEndpointDto, endpoint))).resolves.toEqual([]);
    await expect(validate(plainToInstance(RemoteMcpAssignmentDto, {
      endpointId: "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      toolNames: ["weather.current"],
      timeoutSeconds: 10,
    }))).resolves.toEqual([]);
    expect(await validate(plainToInstance(RemoteMcpAssignmentDto, {
      endpointId: "8af9f870-1d08-4f92-b3fb-59aa46a8e907",
      toolNames: [],
      timeoutSeconds: 4,
    }))).not.toEqual([]);
  });

  it("derives tenant from principal for public registry and assignments", async () => {
    const service = {
      createEndpoint: vi.fn().mockResolvedValue({ id: "endpoint-1" }),
      listAssignments: vi.fn().mockResolvedValue({ items: [] }),
    };
    const endpoints = new RemoteMcpController(service as unknown as RemoteMcpService);
    const assignments = new AgentRemoteMcpController(service as unknown as RemoteMcpService);
    await endpoints.create(endpoint as CreateRemoteMcpEndpointDto, principal, request);
    await assignments.list("agent-1", principal);
    expect(service.createEndpoint).toHaveBeenCalledWith(endpoint, {
      principal,
      requestId: "request-1",
    });
    expect(service.listAssignments).toHaveBeenCalledWith("tenant-1", "agent-1");
  });

  it("does not accept tenant id in the internal resolver contract", async () => {
    const service = { resolve: vi.fn().mockResolvedValue({ configVersion: 4, endpoints: [] }) };
    const controller = new InternalRemoteMcpController(service as unknown as RemoteMcpService);
    await controller.resolve({
      agentId: "ce025684-5f55-49c6-baa6-da53e11fe7ee",
      deviceId: "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      configVersion: 4,
    });
    expect(service.resolve).toHaveBeenCalledWith(
      "ce025684-5f55-49c6-baa6-da53e11fe7ee",
      "4b6fbf00-4072-4ab5-b06e-a2884749d206",
      4,
    );
  });
});
