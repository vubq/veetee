export function canonicalizeRestrictedJcs(value: unknown): string;
export function sha256File(path: string): Promise<string>;
export function signResourceManifest<T extends { signature: { value: string } }>(
  manifest: T,
  privateKeyPath: string,
): Promise<T>;
