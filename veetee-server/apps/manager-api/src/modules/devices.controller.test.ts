import { PATH_METADATA } from "@nestjs/common/constants";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import type { FastifyReply } from "fastify";
import { describe, expect, it, vi } from "vitest";

import type { DeviceConfigService } from "../config/device-config.service.js";
import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { AssignAgentDto, DevicesController, ReportedStateDto } from "./devices.controller.js";

const validReport = {
  version: 12,
  bootId: "95eff5a6-3dcf-4cb4-a6d9-e31cd6d82f63",
  state: {
    schemaVersion: 1,
    firmware: { version: "0.2.0" },
    capabilities: {
      board: "veetee-s3-n16r8",
      display: {
        target: "st7789-240x280-rgb565",
        controller: "st7789",
        width: 240,
        height: 280,
        colorFormat: "rgb565",
        resourceAbi: 2,
        uiAbi: 1,
        slotBytes: 2_097_152,
        hotReload: true,
        compositions: ["signal", "monolith", "quiet"],
      },
      wake: {
        runtime: "esp-sr",
        runtimeAbi: 1,
        resourceAbi: 1,
        slotBytes: 2_097_152,
        sampleRateHz: 16_000,
        channels: 1,
        hotReload: true,
      },
    },
    resource: {
      phase: "downloading",
      currentVersion: "factory-bringup",
      desiredVersion: "1.0.0",
      activeSlot: 0,
      targetSlot: 1,
      expectedBytes: 125_943,
      downloadedBytes: 65_536,
      securityEpoch: 1,
    },
  },
};

describe("DevicesController reported state", () => {
  it("accepts the bounded V1 device report DTO", async () => {
    const input = plainToInstance(ReportedStateDto, validReport);
    await expect(
      validate(input, { whitelist: true, forbidNonWhitelisted: true }),
    ).resolves.toEqual([]);
  });

  it("accepts a UI Pack subsystem report without a wake-resource report", async () => {
    const input = plainToInstance(ReportedStateDto, {
      ...validReport,
      state: {
        ...validReport.state,
        resource: undefined,
        ui: { ...validReport.state.resource, currentVersion: "factory-signal" },
      },
    });
    await expect(
      validate(input, { whitelist: true, forbidNonWhitelisted: true }),
    ).resolves.toEqual([]);
  });

  it("accepts a config-only reconcile report without an artifact subsystem", async () => {
    const input = plainToInstance(ReportedStateDto, {
      ...validReport,
      state: {
        schemaVersion: 1,
        firmware: { version: "0.2.0" },
        config: {
          desiredVersion: 13,
          appliedVersion: 12,
          phase: "applying",
        },
      },
    });
    await expect(
      validate(input, { whitelist: true, forbidNonWhitelisted: true }),
    ).resolves.toEqual([]);
  });

  it("rejects invalid progress and failure semantics", async () => {
    const store = {
      updateReportedState: vi.fn(),
    } as unknown as ControlPlaneStore;
    const controller = new DevicesController(store, {} as DeviceConfigService);
    const invalidProgress = plainToInstance(ReportedStateDto, {
      ...validReport,
      state: {
        ...validReport.state,
        resource: {
          ...validReport.state.resource,
          downloadedBytes: validReport.state.resource.expectedBytes + 1,
        },
      },
    });
    await expect(controller.report("device-1", invalidProgress)).rejects.toThrow(
      /downloadedBytes/,
    );

    const missingError = plainToInstance(ReportedStateDto, {
      ...validReport,
      state: {
        ...validReport.state,
        resource: { ...validReport.state.resource, phase: "failed" },
      },
    });
    await expect(controller.report("device-1", missingError)).rejects.toThrow(
      /errorCode/,
    );

    const ambiguous = plainToInstance(ReportedStateDto, {
      ...validReport,
      state: { ...validReport.state, ui: validReport.state.resource },
    });
    await expect(controller.report("device-1", ambiguous)).rejects.toThrow(
      /exactly one reconcile subsystem/,
    );
  });

  it("validates config version and failure semantics before storing", async () => {
    const store = {
      updateReportedState: vi.fn().mockResolvedValue({ id: "device-1" }),
    } as unknown as ControlPlaneStore;
    const controller = new DevicesController(store, {} as DeviceConfigService);
    const report = (config: Record<string, unknown>) => plainToInstance(ReportedStateDto, {
      ...validReport,
      state: {
        schemaVersion: 1,
        firmware: { version: "0.2.0" },
        config,
      },
    });

    await expect(controller.report("device-1", report({
      desiredVersion: 4,
      appliedVersion: 5,
      phase: "applying",
    }))).rejects.toThrow(/appliedVersion/);
    await expect(controller.report("device-1", report({
      desiredVersion: 5,
      appliedVersion: 4,
      phase: "active",
    }))).rejects.toThrow(/do not match/);
    await expect(controller.report("device-1", report({
      desiredVersion: 5,
      appliedVersion: 4,
      phase: "failed",
    }))).rejects.toThrow(/errorCode/);

    await expect(controller.report("device-1", report({
      desiredVersion: 5,
      appliedVersion: 4,
      phase: "failed",
      errorCode: "signature_invalid",
    }))).resolves.toEqual({ id: "device-1" });
  });

  it("serves only the signed projection and returns 304 for a matching ETag", async () => {
    const body = {
      schema_version: 1 as const,
      device_id: "device-1",
      version: 7,
      wake_profile: null,
      signature: {
        algorithm: "ed25519" as const,
        key_id: "test-key",
        security_epoch: 1,
        value: "signature",
      },
    };
    const deviceConfig = {
      snapshot: vi.fn().mockResolvedValue({
        body,
        etag: "cfg1-current",
        canonicalBody: "{}",
      }),
    } as unknown as DeviceConfigService;
    const store = {
      deviceForAuthenticatedDevice: vi.fn(),
    } as unknown as ControlPlaneStore;
    const headers: Record<string, string> = {};
    let status = 200;
    const reply = {
      header: vi.fn((name: string, value: string) => {
        headers[name] = value;
      }),
      code: vi.fn((value: number) => {
        status = value;
      }),
    } as unknown as FastifyReply;
    const controller = new DevicesController(store, deviceConfig);

    await expect(controller.desired("device-1", undefined, reply)).resolves.toEqual(body);
    expect(headers).toMatchObject({
      ETag: '"cfg1-current"',
      "Cache-Control": "private, no-cache",
    });
    expect(store.deviceForAuthenticatedDevice).not.toHaveBeenCalled();

    await expect(controller.desired("device-1", 'W/"cfg1-current"', reply)).resolves.toBeUndefined();
    expect(status).toBe(304);
    expect(Reflect.getMetadata(PATH_METADATA, DevicesController.prototype.desired)).toBe(
      "veetee/config/v1/devices/:id",
    );
  });

  it("exposes only the canonical Veetee reported-state route", () => {
    const paths = Reflect.getMetadata(
      PATH_METADATA,
      DevicesController.prototype.report,
    ) as string;
    expect(paths).toBe("veetee/devices/:id/reported-state");
  });

  it("validates assistant assignment as an optional published-agent pointer", async () => {
    const empty = plainToInstance(AssignAgentDto, {});
    await expect(
      validate(empty, { whitelist: true, forbidNonWhitelisted: true }),
    ).resolves.toEqual([]);

    const invalid = plainToInstance(AssignAgentDto, { agentId: "not-a-uuid" });
    await expect(validate(invalid, { whitelist: true, forbidNonWhitelisted: true })).resolves.not.toEqual([]);

    const paths = Reflect.getMetadata(
      PATH_METADATA,
      DevicesController.prototype.assignAgent,
    ) as string;
    expect(paths).toBe("api/v1/devices/:id/agent");
  });
});
