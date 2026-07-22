import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it, vi } from "vitest";

import type { Principal } from "../auth/auth.types.js";
import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { AuditController, AuditQueryDto } from "./audit.controller.js";

describe("AuditController", () => {
  it("validates bounded filters and preserves tenant scope", async () => {
    const valid = plainToInstance(AuditQueryDto, { limit: 20, action: "artifact", targetType: "artifact" });
    await expect(validate(valid)).resolves.toEqual([]);

    const store = { listAuditEvents: vi.fn().mockResolvedValue([]) } as unknown as ControlPlaneStore;
    const controller = new AuditController(store);
    await expect(controller.list(valid, { tenantId: "tenant-1" } as Principal)).resolves.toEqual([]);
    expect(store.listAuditEvents).toHaveBeenCalledWith("tenant-1", valid);
  });

  it("rejects unbounded query limits", async () => {
    const invalid = plainToInstance(AuditQueryDto, { limit: 201 });
    expect(await validate(invalid)).not.toEqual([]);
  });
});
