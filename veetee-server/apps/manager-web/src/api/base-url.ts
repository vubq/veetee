export interface BrowserLocationLike {
  protocol: string;
  hostname: string;
}

export function resolveManagerApiBaseUrl(
  configuredUrl?: string,
  location?: BrowserLocationLike,
): string {
  const configured = configuredUrl?.trim();
  if (configured) return configured.replace(/\/$/, "");

  const browserLocation = location ??
    (typeof window !== "undefined" ? window.location : undefined);
  const fallback = browserLocation
    ? `${browserLocation.protocol}//${browserLocation.hostname}:8001`
    : "http://127.0.0.1:8001";
  return fallback.replace(/\/$/, "");
}
