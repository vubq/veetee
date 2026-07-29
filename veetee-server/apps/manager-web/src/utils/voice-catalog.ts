export type VoiceCatalogEntry = Record<string, unknown>;

export interface VoiceMetadataFallback {
  gender: string;
  style: string;
}

export interface ResolvedVoiceMetadata extends VoiceMetadataFallback {
  catalogVoice: VoiceCatalogEntry | undefined;
}

export interface VoiceOptionLabels {
  genders: Record<string, string>;
  styles: Record<string, string>;
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

export function findCatalogVoice(
  voices: VoiceCatalogEntry[],
  voiceId: string,
): VoiceCatalogEntry | undefined {
  return voices.find((voice) => stringValue(voice.id) === voiceId);
}

export function resolveVoiceMetadata(
  voices: VoiceCatalogEntry[],
  voiceId: string,
  fallback: VoiceMetadataFallback,
): ResolvedVoiceMetadata {
  const catalogVoice = findCatalogVoice(voices, voiceId);
  return {
    catalogVoice,
    gender: stringValue(catalogVoice?.gender, fallback.gender),
    style: stringValue(catalogVoice?.style, fallback.style),
  };
}

export function formatVoiceOptionLabel(
  voice: VoiceCatalogEntry,
  labels: VoiceOptionLabels,
): string {
  const id = stringValue(voice.id);
  const parts = [stringValue(voice.label, id)];
  const gender = stringValue(voice.gender);
  const style = stringValue(voice.style);
  if (gender) parts.push(labels.genders[gender] ?? gender);
  if (style) parts.push(labels.styles[style] ?? style);
  return parts.filter(Boolean).join(" · ");
}
