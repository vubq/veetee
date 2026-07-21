import { ConflictException, Logger } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import type { AuditService } from "../audit/audit.service.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import type { VoiceMcpService } from "../mcp/voice-mcp.service.js";
import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { McpController } from "./mcp.controller.js";

const principal: Principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "veetee",
  role: TenantRole.OPERATOR,
  email: "operator@example.test",
  displayName: "Operator",
};

const request = { id: "request-1", headers: {} } as RequestWithPrincipal;
const userTool = {
  name: "self.get_system_info",
  description: "Read diagnostic system information.",
  inputSchema: { type: "object", properties: {}, additionalProperties: false },
  audience: "user" as const,
  safetyClass: "read_only" as const,
  requiresConfirmation: true,
};

function harness() {
  const store = { device: vi.fn().mockResolvedValue({ id: "device-1" }) };
  const voice = {
    listTools: vi.fn().mockResolvedValue([userTool]),
    callTool: vi.fn().mockResolvedValue({ tool: userTool.name, result: { isError: false } }),
  };
  const audit = { record: vi.fn().mockResolvedValue(undefined) };
  return {
    store,
    voice,
    audit,
    controller: new McpController(
      store as unknown as ControlPlaneStore,
      voice as unknown as VoiceMcpService,
      audit as unknown as AuditService,
    ),
  };
}

describe("McpController", () => {
  it("checks tenant ownership before proxying the device catalog", async () => {
    const { controller, store, voice } = harness();
    await controller.listDeviceTools("device-1", principal);
    expect(store.device).toHaveBeenCalledWith("tenant-1", "device-1");
    expect(store.device.mock.invocationCallOrder[0]).toBeLessThan(
      voice.listTools.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER,
    );
  });

  it("blocks a user-only tool without explicit confirmation", async () => {
    const { controller, voice, audit } = harness();
    await expect(
      controller.callDeviceTool(
        "device-1",
        { name: userTool.name },
        { arguments: {}, confirmed: false, timeoutSeconds: 10 },
        principal,
        request,
      ),
    ).rejects.toBeInstanceOf(ConflictException);
    expect(voice.callTool).not.toHaveBeenCalled();
    expect(audit.record).not.toHaveBeenCalled();
  });

  it("writes a request audit before dispatch and records the outcome", async () => {
    const { controller, voice, audit } = harness();
    await controller.callDeviceTool(
      "device-1",
      { name: userTool.name },
      { arguments: {}, confirmed: true, timeoutSeconds: 4 },
      principal,
      request,
    );
    expect(audit.record.mock.calls.map(([value]) => value.action)).toEqual([
      "device.mcp.call.requested",
      "device.mcp.call.succeeded",
    ]);
    expect(audit.record.mock.invocationCallOrder[0]).toBeLessThan(
      voice.callTool.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER,
    );
  });

  it("does not dispatch when the required pre-call audit cannot be stored", async () => {
    const { controller, voice, audit } = harness();
    audit.record.mockRejectedValueOnce(new Error("database unavailable"));
    await expect(
      controller.callDeviceTool(
        "device-1",
        { name: userTool.name },
        { arguments: {}, confirmed: true, timeoutSeconds: 4 },
        principal,
        request,
      ),
    ).rejects.toThrow("database unavailable");
    expect(voice.callTool).not.toHaveBeenCalled();
  });

  it("returns a completed side effect even if only the outcome audit fails", async () => {
    const { controller, audit } = harness();
    audit.record.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error("audit lag"));
    const logger = vi.spyOn(Logger.prototype, "error").mockImplementation(() => undefined);
    await expect(
      controller.callDeviceTool(
        "device-1",
        { name: userTool.name },
        { arguments: {}, confirmed: true, timeoutSeconds: 4 },
        principal,
        request,
      ),
    ).resolves.toMatchObject({ tool: userTool.name });
    expect(logger).toHaveBeenCalled();
  });
});
