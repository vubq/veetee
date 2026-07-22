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

  it("keeps localhost as the server-side fallback", () => {
    expect(resolveManagerApiBaseUrl(undefined)).toBe("http://127.0.0.1:8001");
  });
});
