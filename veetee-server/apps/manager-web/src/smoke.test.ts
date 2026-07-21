import { describe, expect, it } from "vitest";

import prototypePage from "../../../prototypes/manager-web/index.html?raw";

describe("approved manager prototype", () => {
  it("keeps all primary product pages", () => {
    for (const page of ["overview", "devices", "agents", "providers", "lab", "mcp", "ota"]) {
      expect(prototypePage).toContain(`data-page="${page}"`);
    }
  });
});
