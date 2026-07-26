import { describe, expect, it } from "vitest";

import { voiceQualityWarnings } from "./voice-quality";

describe("VieNeu voice quality warnings", () => {
  it("warns that 1.2x can reduce Vietnamese clarity", () => {
    expect(
      voiceQualityWarnings({
        adapter: "vieneu-local",
        rate: 1.2,
        volume: 1,
        sourceStyle: "tu_nhien",
        selectedStyle: "tu_nhien",
      }),
    ).toEqual([expect.stringContaining("phụ âm và dấu tiếng Việt")]);
  });

  it("warns when a storytelling reference is forced into conversation style", () => {
    expect(
      voiceQualityWarnings({
        adapter: "vieneu-local",
        rate: 1,
        volume: 1,
        sourceStyle: "doc_truyen",
        selectedStyle: "tu_nhien",
      }),
    ).toEqual([expect.stringContaining("Đọc truyện")]);
  });

  it("does not apply VieNeu-specific warnings to another adapter", () => {
    expect(
      voiceQualityWarnings({
        adapter: "other-tts",
        rate: 1.5,
        volume: 1.5,
        sourceStyle: "doc_truyen",
        selectedStyle: "tu_nhien",
      }),
    ).toEqual([]);
  });
});
