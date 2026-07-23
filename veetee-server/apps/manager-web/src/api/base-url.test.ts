import { describe, expect, it } from "vitest";

import { resolveManagerApiBaseUrl } from "./base-url";

describe("resolveManagerApiBaseUrl", () => {
  it("uses an explicit API URL when configured", () => {
    expect(resolveManagerApiBaseUrl(" https://api.example.test:8443/ ")).toBe(
      "https://api.example.test:8443",
    );
  });

  it("targets the host serving Manager Web for LAN clients", () => {
    expect(resolveManagerApiBaseUrl(undefined, {
      protocol: "http:",
      hostname: "192.168.110.115",
    })).toBe("http://192.168.110.115:8001");
  });

  it("uses the same-origin proxy for HTTPS tunnel clients", () => {
    expect(resolveManagerApiBaseUrl(undefined, {
      protocol: "https:",
      hostname: "veetee-dev.tail52a635.ts.net",
    })).toBe("");
  });

  it("keeps localhost as the server-side fallback", () => {
    expect(resolveManagerApiBaseUrl(undefined)).toBe("http://127.0.0.1:8001");
  });
});
