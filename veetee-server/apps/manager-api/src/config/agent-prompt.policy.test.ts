import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { BadRequestException } from "@nestjs/common";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_AGENT_BASE_PROMPT,
  PERSONALITY_PRESETS,
  agentPromptCatalog,
  normalizePublishedAgentPrompt,
  validateAgentPromptDraft,
  validatePromptTemplate,
} from "./agent-prompt.policy.js";

describe("agent prompt policy", () => {
  it("keeps the Manager default synchronized with voice-server agent-base-prompt.txt", () => {
    const voiceTemplate = readFileSync(
      resolve(
        __dirname,
        "../../../voice-server/src/veetee_voice_server/prompts/agent-base-prompt.txt",
      ),
      "utf8",
    ).trim();
    expect(DEFAULT_AGENT_BASE_PROMPT).toBe(voiceTemplate);
  });

  it("exposes a diverse versioned personality catalog", () => {
    const catalog = agentPromptCatalog();
    expect(catalog.schemaVersion).toBe(1);
    expect(catalog.catalogVersion).toBe(1);
    expect(catalog.personalityPresets).toHaveLength(PERSONALITY_PRESETS.length);
    expect(catalog.personalityPresets.length).toBeGreaterThanOrEqual(15);
    expect(catalog.personalityPresets.map(({ id }) => id)).toEqual(
      expect.arrayContaining(["stubborn-reasoned", "spirited-debater", "scientific-curious"]),
    );
    expect(catalog.variables.map(({ name }) => name)).toEqual(
      expect.arrayContaining([
        "agent_name",
        "language",
        "personality",
        "current_time",
        "available_tools",
      ]),
    );
  });

  it("normalizes a legacy agent to a complete immutable prompt snapshot", () => {
    const prompt = normalizePublishedAgentPrompt(undefined, { locale: "vi-VN" });
    expect(prompt).toMatchObject({
      schemaVersion: 1,
      catalogVersion: 1,
      template: DEFAULT_AGENT_BASE_PROMPT,
      language: "vi-VN",
      timeZone: "Asia/Bangkok",
      personalityPresetId: "warm-empathetic",
    });
    expect(prompt.personality).toContain("ấm áp");
    expect(prompt.allowedVariables).toContain("agent_name");
  });

  it("freezes the selected preset and custom personality as data", () => {
    const prompt = normalizePublishedAgentPrompt(
      {
        schemaVersion: 1,
        template: DEFAULT_AGENT_BASE_PROMPT,
        language: "Tiếng Việt tự nhiên",
        timeZone: "Asia/Ho_Chi_Minh",
        personalityPresetId: "spirited-debater",
        customPersonality: "Bắt bẻ vui, nhưng dừng ngay khi người dùng không thoải mái.",
        responseStyle: "Ngắn và trực diện.",
        userAddress: "bạn",
      },
      { locale: "vi-VN" },
    );
    expect(prompt.personalityLabel).toBe("Cãi tay đôi");
    expect(prompt.personality).toContain("Phản biện luận điểm");
    expect(prompt.personality).toContain("Bắt bẻ vui");
  });

  it.each([
    ["unknown preset", { personalityPresetId: "does-not-exist" }],
    ["invalid time zone", { timeZone: "Moon/Sea-of-Tranquility" }],
    ["unknown variable", { template: `${DEFAULT_AGENT_BASE_PROMPT}\n{{provider.secret}}` }],
    ["expression", { template: `${DEFAULT_AGENT_BASE_PROMPT}\n{{agent_name | upper}}` }],
    ["unclosed token", { template: `${DEFAULT_AGENT_BASE_PROMPT}\n{{current_time` }],
    [
      "missing required variable",
      { template: DEFAULT_AGENT_BASE_PROMPT.replaceAll("{{language}}", "Tiếng Việt") },
    ],
  ])("rejects %s", (_label, patch) => {
    expect(() =>
      validateAgentPromptDraft({
        schemaVersion: 1,
        template: DEFAULT_AGENT_BASE_PROMPT,
        language: "Tiếng Việt",
        timeZone: "Asia/Bangkok",
        personalityPresetId: "warm-empathetic",
        customPersonality: "",
        responseStyle: "",
        userAddress: "",
        ...patch,
      }),
    ).toThrow(BadRequestException);
  });

  it("does not interpret arbitrary template expressions", () => {
    expect(() =>
      validatePromptTemplate(
        "{{agent_name}} {{language}} {{persona}} {{personality}} {{constructor.constructor()}}",
      ),
    ).toThrow(BadRequestException);
  });
});
