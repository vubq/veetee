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
  // HTTPS dev/tunnel hosts terminate TLS at the web edge; use the same-origin
  // Vite proxy instead of sending mixed-content requests to HTTP:8001.
  if (browserLocation?.protocol === "https:") return "";
  const fallback = browserLocation
    ? `${browserLocation.protocol}//${browserLocation.hostname}:8001`
    : "http://127.0.0.1:8001";
  return fallback.replace(/\/$/, "");
}
