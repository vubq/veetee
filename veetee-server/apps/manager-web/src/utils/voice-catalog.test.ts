import { describe, expect, it } from "vitest";

import { formatVoiceOptionLabel, resolveVoiceMetadata } from "./voice-catalog";

const voices = [
  { id: "Trúc Ly", label: "Trúc Ly", gender: "female", style: "tu_nhien" },
  { id: "Ngọc Linh", label: "Ngọc Linh", gender: "female", style: "doc_truyen" },
];

describe("voice catalog metadata", () => {
  it("uses the selected catalog voice instead of stale draft metadata", () => {
    expect(
      resolveVoiceMetadata(voices, "Ngọc Linh", {
        gender: "male",
        style: "tin_tuc",
      }),
    ).toMatchObject({
      gender: "female",
      style: "doc_truyen",
    });
  });

  it("preserves legacy metadata when a custom voice is absent from the catalog", () => {
    expect(
      resolveVoiceMetadata(voices, "Giọng tùy chỉnh", {
        gender: "neutral",
        style: "legacy_style",
      }),
    ).toEqual({
      catalogVoice: undefined,
      gender: "neutral",
      style: "legacy_style",
    });
  });

  it("formats localized gender and source style in each voice option", () => {
    expect(
      formatVoiceOptionLabel(voices[0]!, {
        genders: { female: "Nữ" },
        styles: { tu_nhien: "Tự nhiên / hội thoại" },
      }),
    ).toBe("Trúc Ly · Nữ · Tự nhiên / hội thoại");
  });
});
