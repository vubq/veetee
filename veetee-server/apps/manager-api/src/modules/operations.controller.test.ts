import { describe, expect, it, vi } from "vitest";

import { OperationsController } from "./operations.controller.js";

describe("OperationsController", () => {
  it("reports the LAN-first policy without exposing secrets", () => {
    vi.stubEnv("VEETEE_MANAGER_PUBLIC_URL", "http://192.168.110.115:8001");
    vi.stubEnv("VEETEE_VOICE_WS_URL", "ws://192.168.110.115:8000/veetee/v1/");
    vi.stubEnv("VEETEE_CONVERSATION_EVENT_RETENTION_DAYS", "45");
    const profile = new OperationsController().profile({} as never);
    expect(profile.deployment.domainRequired).toBe(false);
    expect(profile.deployment.managerApiUrl).toContain("192.168.110.115");
    expect(profile.privacy.conversationEventRetentionDays).toBe(30);
    expect(JSON.stringify(profile)).not.toMatch(/Bearer|password=|api[_-]?key/i);
    vi.unstubAllEnvs();
  });
});
