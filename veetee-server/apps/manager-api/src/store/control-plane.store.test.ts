import { describe, expect, it } from "vitest";

import { ControlPlaneStore } from "./control-plane.store.js";

describe("ControlPlaneStore", () => {
  it("creates a six digit single-use pairing code", () => {
    const store = new ControlPlaneStore();
    const ticket = store.createPairingCode("esp32-test");
    expect(ticket.code).toMatch(/^\d{6}$/);
    const device = store.claimPairing(ticket.code, "Veetee Test", "agent-veetee-vi");
    expect(device.hardwareId).toBe("esp32-test");
    expect(() => store.claimPairing(ticket.code, "Duplicate")).toThrow();
  });

  it("keeps desired and reported state separate", () => {
    const store = new ControlPlaneStore();
    const ticket = store.createPairingCode("esp32-state");
    const device = store.claimPairing(ticket.code, "Veetee State");
    store.setDesiredState(device.id, { resourceVersion: "2" });
    store.updateReportedState(device.id, { resourceVersion: "1" });
    expect(store.device(device.id).desiredState).not.toEqual(store.device(device.id).reportedState);
  });
});
