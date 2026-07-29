import { describe, expect, it } from "vitest";

import {
  canManageRemoteMcp,
  canTestRemoteMcp,
  canUseMemory,
  hasMinimumRole,
} from "./role-capabilities";

describe("tenant role capabilities", () => {
  it("matches the Manager API role hierarchy", () => {
    expect(hasMinimumRole(undefined, "VIEWER")).toBe(false);
    expect(hasMinimumRole("VIEWER", "VIEWER")).toBe(true);
    expect(hasMinimumRole("OPERATOR", "ADMIN")).toBe(false);
    expect(hasMinimumRole("OWNER", "ADMIN")).toBe(true);
  });

  it("keeps memory, Remote MCP tests and Remote MCP mutations at their API roles", () => {
    expect(canUseMemory("VIEWER")).toBe(false);
    expect(canUseMemory("OPERATOR")).toBe(true);
    expect(canTestRemoteMcp("VIEWER")).toBe(false);
    expect(canTestRemoteMcp("OPERATOR")).toBe(true);
    expect(canManageRemoteMcp("OPERATOR")).toBe(false);
    expect(canManageRemoteMcp("ADMIN")).toBe(true);
    expect(canManageRemoteMcp("OWNER")).toBe(true);
  });
});
