import { PATH_METADATA } from "@nestjs/common/constants";
import { plainToInstance } from "class-transformer";
import { validate } from "class-validator";
import { describe, expect, it, vi } from "vitest";

import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { DevicesController, ReportedStateDto } from "./devices.controller.js";

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

  it("rejects invalid progress and failure semantics", async () => {
    const store = {
      updateReportedState: vi.fn(),
    } as unknown as ControlPlaneStore;
    const controller = new DevicesController(store);
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
      /exactly one artifact subsystem/,
    );
  });

  it("exposes only the canonical Veetee reported-state route", () => {
    const paths = Reflect.getMetadata(
      PATH_METADATA,
      DevicesController.prototype.report,
    ) as string;
    expect(paths).toBe("veetee/devices/:id/reported-state");
  });
});
