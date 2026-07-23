import { TenantRole } from "@prisma/client";
import { describe, expect, it, vi } from "vitest";

import type { AuditService } from "../audit/audit.service.js";
import type { Principal, RequestWithPrincipal } from "../auth/auth.types.js";
import type { DeviceDiagnosticsService } from "../diagnostics/device-diagnostics.service.js";
import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { DeviceDiagnosticsController } from "./device-diagnostics.controller.js";

const principal: Principal = {
  userId: "user-1",
  tenantId: "tenant-1",
  tenantSlug: "veetee",
  role: TenantRole.OPERATOR,
  email: "operator@example.test",
  displayName: "Operator",
};
const request = { id: "request-1" } as RequestWithPrincipal;

function harness() {
  const store = { device: vi.fn().mockResolvedValue({ id: "device-1" }) };
  const diagnostics = {
    health: vi.fn().mockResolvedValue({ schemaVersion: 1 }),
    startAudio: vi.fn().mockResolvedValue({ state: "running" }),
    selfTest: vi.fn().mockResolvedValue({ overall: "pass" }),
  };
  const audit = { record: vi.fn().mockResolvedValue(undefined) };
  return {
    store,
    diagnostics,
    audit,
    controller: new DeviceDiagnosticsController(
      store as unknown as ControlPlaneStore,
      diagnostics as unknown as DeviceDiagnosticsService,
      audit as unknown as AuditService,
    ),
  };
}

describe("DeviceDiagnosticsController", () => {
  it("guards health lookup by tenant before proxying", async () => {
    const { controller, store, diagnostics } = harness();
    await controller.health("device-1", principal);
    expect(store.device).toHaveBeenCalledWith("tenant-1", "device-1");
    expect(store.device.mock.invocationCallOrder[0]).toBeLessThan(
      diagnostics.health.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER,
    );
  });

  it("confirms and audits audio diagnostic start", async () => {
    const { controller, diagnostics, audit } = harness();
    await controller.startAudio(
      "device-1",
      { durationSeconds: 5 },
      principal,
      request,
    );
    expect(diagnostics.startAudio).toHaveBeenCalledWith("device-1", 5);
    expect(audit.record.mock.calls.map(([value]) => value.action)).toEqual([
      "device.diagnostics.audio.requested",
      "device.diagnostics.audio.succeeded",
    ]);
    const requested = audit.record.mock.calls[0]?.[0] as {
      details?: { rawAudioStored?: boolean };
    };
    expect(requested.details?.rawAudioStored).toBe(false);
  });

  it("records a failed self-test without hiding the device error", async () => {
    const { controller, diagnostics, audit } = harness();
    diagnostics.selfTest.mockRejectedValueOnce(new Error("device unavailable"));
    await expect(
      controller.selfTest("device-1", principal, request),
    ).rejects.toThrow("device unavailable");
    expect(audit.record.mock.calls.map(([value]) => value.action)).toEqual([
      "device.diagnostics.self_test.requested",
      "device.diagnostics.self_test.failed",
    ]);
  });
});
