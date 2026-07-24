import { describe, expect, it } from "vitest";

import {
  hasAuthoritativeVoiceCatalogDrift,
  hasProviderConfigVersionUpgrade,
} from "./seed.js";

describe("provider seed config", () => {
  it("refreshes an authoritative voice catalog when a removed voice remains persisted", () => {
    const current = [{ id: "voice-a" }, { id: "removed-voice" }];
    const canonical = [{ id: "voice-a" }];

    expect(hasAuthoritativeVoiceCatalogDrift(current, canonical, true)).toBe(true);
    expect(hasAuthoritativeVoiceCatalogDrift(canonical, canonical, true)).toBe(false);
    expect(hasAuthoritativeVoiceCatalogDrift(current, canonical, false)).toBe(false);
  });

  it("upgrades built-in operational defaults only when the target version advances", () => {
    expect(hasProviderConfigVersionUpgrade(undefined, 3)).toBe(true);
    expect(hasProviderConfigVersionUpgrade(2, 3)).toBe(true);
    expect(hasProviderConfigVersionUpgrade(3, 3)).toBe(false);
    expect(hasProviderConfigVersionUpgrade(4, 3)).toBe(false);
  });
});
