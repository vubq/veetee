import { describe, expect, it } from "vitest";

import type { Device } from "../api/schemas";
import { summarizeDeviceDelivery } from "./device-delivery";

function device(
  desiredState: Record<string, unknown>,
  reportedState: Record<string, unknown>,
  desiredVersion = 1,
  reportedVersion = 9,
): Device {
  return {
    id: "device-1",
    hardwareId: "ABC123",
    name: "Veetee Lab",
    status: "online",
    desiredState: { version: desiredVersion, state: desiredState },
    reportedState: { version: reportedVersion, state: reportedState },
    pairedAt: "2026-07-22T00:00:00.000Z",
  };
}

describe("summarizeDeviceDelivery", () => {
  it("does not compare desired revision with reported sequence", () => {
    const result = summarizeDeviceDelivery(device({}, {}, 1, 42));

    expect(result.state).toBe("unmanaged");
  });

  it("accepts an active UI Pack with matching semantic version", () => {
    const result = summarizeDeviceDelivery(device(
      { uiPackVersion: "1.1.0" },
      { ui: { phase: "active", currentVersion: "1.1.0", desiredVersion: "1.1.0" } },
      2,
      17,
    ));

    expect(result.state).toBe("synced");
    expect(result.subsystems.find((item) => item.id === "ui")?.state).toBe("synced");
  });

  it("keeps an in-progress resource rollout pending", () => {
    const result = summarizeDeviceDelivery(device(
      { resourceBundleVersion: "2.0.0" },
      { resource: { phase: "downloading", currentVersion: "1.0.0", desiredVersion: "2.0.0" } },
    ));

    expect(result.state).toBe("pending");
  });

  it("surfaces terminal firmware failures", () => {
    const result = summarizeDeviceDelivery(device(
      { uiPackVersion: "2.0.0" },
      { ui: { phase: "failed", currentVersion: "1.1.0", desiredVersion: "2.0.0" } },
    ));

    expect(result.state).toBe("failed");
  });
});
