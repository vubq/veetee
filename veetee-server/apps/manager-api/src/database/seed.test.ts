import { describe, expect, it } from "vitest";

import { hasAuthoritativeVoiceCatalogDrift } from "./seed.js";

describe("provider seed config", () => {
  it("refreshes an authoritative voice catalog when a removed voice remains persisted", () => {
    const current = [{ id: "voice-a" }, { id: "removed-voice" }];
    const canonical = [{ id: "voice-a" }];

    expect(hasAuthoritativeVoiceCatalogDrift(current, canonical, true)).toBe(true);
    expect(hasAuthoritativeVoiceCatalogDrift(canonical, canonical, true)).toBe(false);
    expect(hasAuthoritativeVoiceCatalogDrift(current, canonical, false)).toBe(false);
  });
});
