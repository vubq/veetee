import { describe, expect, it } from "vitest";

import { voiceQualityWarnings } from "./voice-quality";

describe("VieNeu voice quality warnings", () => {
  it("warns that 1.2x can reduce Vietnamese clarity", () => {
    expect(
      voiceQualityWarnings({
        adapter: "vieneu-local",
        rate: 1.2,
        volume: 1,
      }),
    ).toEqual([expect.stringContaining("phụ âm và dấu tiếng Việt")]);
  });

  it("warns when PCM volume is amplified above 1.0", () => {
    expect(
      voiceQualityWarnings({
        adapter: "vieneu-local",
        rate: 1,
        volume: 1.05,
      }),
    ).toEqual([expect.stringContaining("nguy cơ clipping")]);
  });

  it("does not apply VieNeu-specific warnings to another adapter", () => {
    expect(
      voiceQualityWarnings({
        adapter: "other-tts",
        rate: 1.5,
        volume: 1.5,
      }),
    ).toEqual([]);
  });
});
