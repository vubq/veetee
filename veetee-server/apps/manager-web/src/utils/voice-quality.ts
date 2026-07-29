export interface VoiceQualityProfile {
  adapter: string;
  rate: number;
  volume: number;
}

export function voiceQualityWarnings(profile: VoiceQualityProfile): string[] {
  if (!profile.adapter.toLowerCase().includes("vieneu")) return [];
  const warnings: string[] = [];
  if (profile.rate >= 1.2) {
    warnings.push(
      "Từ 1,2×, WSOLA có thể làm phụ âm và dấu tiếng Việt kém rõ; 1,0× cho độ rõ tốt nhất. Tốc độ này cũng có thể nhanh hơn khả năng sinh của CPU khi máy nóng hoặc câu trả lời rất dài.",
    );
  }
  if (profile.volume > 1) {
    warnings.push(
      "Âm lượng trên 1,0 khuếch đại PCM và có nguy cơ clipping. Ưu tiên chỉnh âm lượng loa trên thiết bị.",
    );
  }
  return warnings;
}
