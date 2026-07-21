import { BadRequestException } from "@nestjs/common";
import { describe, expect, it } from "vitest";

import { expandProviderChains, validateAgentDraftConfig } from "./agent-config.policy.js";

describe("validateAgentDraftConfig", () => {
  it("accepts bounded conversation policy and extension fields", () => {
    expect(() =>
      validateAgentDraftConfig({
        conversation: {
          firstInputSeconds: 15,
          betweenTurnsSeconds: 30,
          closingGraceSeconds: 5,
          maxSessionSeconds: 600,
          timeoutGoodbye: "Tạm biệt, hẹn gặp lại.",
          futurePolicy: { enabled: true },
        },
      }),
    ).not.toThrow();
  });

  it.each([
    ["firstInputSeconds", 0],
    ["betweenTurnsSeconds", Number.NaN],
    ["closingGraceSeconds", 61],
    ["maxSessionSeconds", 3_601],
    ["plannerSeconds", "8"],
  ])("rejects unsafe %s values", (field, value) => {
    expect(() =>
      validateAgentDraftConfig({ conversation: { [field]: value } }),
    ).toThrow(BadRequestException);
  });

  it("rejects empty timeout goodbye text", () => {
    expect(() =>
      validateAgentDraftConfig({ conversation: { timeoutGoodbye: "   " } }),
    ).toThrow(BadRequestException);
  });

  it("expands only explicitly bound providers in declared fallback order", () => {
    const chains = expandProviderChains(
      {
        providerChains: [
          { kind: "vad", locale: "vi-VN", providerIds: ["vad-1"] },
          { kind: "asr", locale: "vi-VN", providerIds: ["asr-1", "asr-2"] },
          { kind: "llm", locale: "vi-VN", providerIds: ["llm-1"] },
          { kind: "tts", locale: "vi-VN", providerIds: ["tts-1"] },
        ],
      },
      [
        binding("vad-1", "vad"),
        binding("asr-1", "asr"),
        binding("asr-2", "asr"),
        binding("llm-1", "llm"),
        binding("tts-1", "tts"),
        binding("unused", "llm"),
      ],
      "vi-VN",
      "auto",
    );
    expect(chains.find((chain) => chain.kind === "asr")?.providers.map(({ id }) => id)).toEqual([
      "asr-1",
      "asr-2",
    ]);
    expect(chains.flatMap((chain) => chain.providers).some(({ id }) => id === "unused")).toBe(false);
  });

  it("rejects disabled, cross-kind and incomplete provider chains", () => {
    expect(() =>
      expandProviderChains(
        { providerChains: [{ kind: "llm", locale: "vi-VN", providerIds: ["asr-1"] }] },
        [binding("asr-1", "asr")],
        "vi-VN",
        "auto",
      ),
    ).toThrow(BadRequestException);
    expect(() =>
      expandProviderChains(
        { providerChains: [{ kind: "llm", locale: "vi-VN", providerIds: ["llm-1"] }] },
        [{ ...binding("llm-1", "llm"), enabled: false }],
        "vi-VN",
        "auto",
      ),
    ).toThrow(BadRequestException);
  });
});

function binding(id: string, kind: "vad" | "asr" | "llm" | "tts") {
  return {
    id,
    kind,
    adapter: `${kind}-adapter`,
    model: `${kind}-model`,
    secretConfigured: false,
    enabled: true,
    priority: 100,
    locales: ["vi-VN"],
  };
}
