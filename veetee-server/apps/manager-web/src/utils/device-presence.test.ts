import { describe, expect, it } from "vitest";

import type { Device } from "../api/schemas";
import { devicePresence, preferredDevice } from "./device-presence";

const base: Device = {
  id: "device-1",
  hardwareId: "ABC123",
  name: "Veetee",
  status: "online",
  desiredState: { version: 1, state: {} },
  reportedState: { version: 2, state: {} },
  pairedAt: "2026-07-22T00:00:00.000Z",
};

describe("devicePresence", () => {
  it("keeps a recent online report green", () => {
    expect(devicePresence({ ...base, lastSeenAt: "2026-07-22T10:00:00.000Z" }, Date.parse("2026-07-22T10:05:00.000Z")).state).toBe("online");
  });

  it("does not present an old online snapshot as a live connection", () => {
    expect(devicePresence({ ...base, lastSeenAt: "2026-07-22T10:00:00.000Z" }, Date.parse("2026-07-22T10:20:01.000Z")).state).toBe("stale");
  });

  it("prefers a fresh device over a stale snapshot", () => {
    const now = Date.parse("2026-07-23T02:00:00.000Z");
    const stale = { ...base, id: "stale", pairedAt: "2026-07-23T01:59:00.000Z", lastSeenAt: "2026-07-22T20:00:00.000Z" };
    const fresh = { ...base, id: "fresh", pairedAt: "2026-07-20T00:00:00.000Z", lastSeenAt: "2026-07-23T01:58:00.000Z" };
    expect(preferredDevice([stale, fresh], now)?.id).toBe("fresh");
  });
});
