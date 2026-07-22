export interface UiPackManifest {
  schema_version: 1;
  kind: "ui_pack";
  id: string;
  version: string;
  theme_id: string;
  channel: "development" | "canary" | "stable";
  license: string;
  target: { board: string; display: string };
  compatibility: {
    resource_abi: 2;
    ui_abi: 1;
    min_firmware: string;
    max_firmware_exclusive: string;
  };
  locales: string[];
  fallback_theme_id: string;
}

export interface InspectedUiPack {
  manifest: UiPackManifest;
  theme: Record<string, unknown>;
  entries: Array<{
    name: string;
    kind: number;
    offset: number;
    bytes: number;
    sha256: string;
    alignment: number;
  }>;
  sizeBytes: number;
}

export const UI_PACK_MAX_BYTES: number;
export function buildUiPack(sourceDirectory: string): Promise<{
  buffer: Buffer;
  manifest: UiPackManifest;
  theme: Record<string, unknown>;
  members: Array<{ name: string; kind: number; bytes: number }>;
}>;
export function inspectUiPackBuffer(buffer: Buffer): Promise<InspectedUiPack>;
export function inspectUiPackReader(
  readRange: (offset: number, length: number) => Promise<Buffer>,
  fileSize: number,
): Promise<InspectedUiPack>;
export function inspectUiPackFile(path: string): Promise<InspectedUiPack>;
export function suggestedUiPackFileName(manifest: UiPackManifest): string;
