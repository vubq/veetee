import { BadRequestException } from "@nestjs/common";
import { describe, expect, it } from "vitest";

import { validateAgentDraftConfig } from "./agent-config.policy.js";

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
});
