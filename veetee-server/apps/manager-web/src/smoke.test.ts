import { describe, expect, it } from "vitest";

import prototypePage from "../../../prototypes/manager-web/index.html?raw";

describe("approved manager prototype", () => {
  it("keeps all primary product pages", () => {
    for (const page of [
      "overview",
      "devices",
      "device-ui",
      "agents",
      "providers",
      "lab",
      "mcp",
      "ota",
    ]) {
      expect(prototypePage).toContain(`data-page="${page}"`);
    }
  });

  it("keeps Signal as the built-in default and exposes UI Pack ingest", () => {
    expect(prototypePage).toContain('data-ui-preview data-theme="signal"');
    expect(prototypePage).toContain('data-ui-theme="monolith"');
    expect(prototypePage).toContain('data-ui-theme="quiet"');
    expect(prototypePage).toContain("data-ui-pack-file");
  });
});
