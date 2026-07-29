import { describe, expect, it } from "vitest";

import {
  defaultAgentConfig,
  hasAuthoritativeVoiceCatalogDrift,
  hasProviderConfigVersionUpgrade,
  shouldInitializeProviderChains,
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

  it("never overwrites an agent provider chain during bootstrap", () => {
    expect(shouldInitializeProviderChains({})).toBe(true);
    expect(shouldInitializeProviderChains({ providerChains: [] })).toBe(true);
    expect(
      shouldInitializeProviderChains({
        providerChains: [
          {
            kind: "llm",
            locale: "vi-VN",
            providerIds: ["user-selected-provider"],
          },
        ],
      }),
    ).toBe(false);
  });

  it("uses CLIProxyAPI with Groq fallback for a clean local agent", () => {
    const config = defaultAgentConfig({
      vad: "vad-provider",
      asr: "asr-provider",
      llm: "cliproxy-provider",
      llmFallback: "groq-provider",
      tts: "tts-provider",
    });
    const chains = config.providerChains as Array<{
      kind: string;
      providerIds: string[];
    }>;

    expect(chains.find((chain) => chain.kind === "llm")?.providerIds).toEqual([
      "cliproxy-provider",
      "groq-provider",
    ]);
  });
});
