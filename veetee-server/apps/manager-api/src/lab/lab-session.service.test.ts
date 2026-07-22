import { UnauthorizedException } from "@nestjs/common";
import { TenantRole } from "@prisma/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LabSessionService } from "./lab-session.service.js";

const tenantId = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const agentId = "33333333-3333-4333-8333-333333333333";
const deviceId = "44444444-4444-4444-8444-444444444444";

describe("LabSessionService", () => {
  const originalSecret = process.env.VEETEE_LAB_TOKEN_SECRET;
  const originalWsUrl = process.env.VEETEE_VOICE_LAB_WS_URL;

  beforeEach(() => {
    process.env.VEETEE_LAB_TOKEN_SECRET = "test-lab-token-secret-that-is-long-enough";
    process.env.VEETEE_VOICE_LAB_WS_URL = "ws://192.0.2.10:8000/veetee/lab/v1/";
  });

  afterEach(() => {
    if (originalSecret === undefined) delete process.env.VEETEE_LAB_TOKEN_SECRET;
    else process.env.VEETEE_LAB_TOKEN_SECRET = originalSecret;
    if (originalWsUrl === undefined) delete process.env.VEETEE_VOICE_LAB_WS_URL;
    else process.env.VEETEE_VOICE_LAB_WS_URL = originalWsUrl;
  });

  it("issues one-use tenant-scoped sessions and rejects replay", async () => {
    const stored = new Map<string, string>();
    const counters = new Map<string, number>();
    const redis = {
      client: {
        set: vi.fn(async (key: string, value: string) => {
          if (stored.has(key)) return null;
          stored.set(key, value);
          return "OK";
        }),
        getdel: vi.fn(async (key: string) => {
          const value = stored.get(key) ?? null;
          stored.delete(key);
          return value;
        }),
        incr: vi.fn(async (key: string) => {
          const value = (counters.get(key) ?? 0) + 1;
          counters.set(key, value);
          return value;
        }),
        expire: vi.fn(async () => 1),
      },
    };
    const prisma = {
      agent: {
        findFirst: vi.fn(async () => ({
          id: agentId,
          name: "Veetee Việt",
          defaultLocale: "vi-VN",
          interactionMode: "AUTO",
          publishedVersion: 7,
        })),
      },
      device: { findFirst: vi.fn(async () => ({ id: deviceId })) },
    };
    const audit = { record: vi.fn(async () => undefined) };
    const service = new LabSessionService(prisma as never, redis as never, audit as never);
    const principal = {
      userId,
      tenantId,
      tenantSlug: "veetee-local",
      role: TenantRole.OWNER,
      email: "owner@veetee.local",
      displayName: "Owner",
    };

    const issued = await service.create(
      {
        agentId,
        inputMode: "audio_replay",
        mcpMode: "selected_device",
        deviceId,
      },
      principal,
      "request-1",
    );
    const consumed = await service.consume(String(issued.token));

    expect(issued).toMatchObject({
      websocketUrl: "ws://192.0.2.10:8000/veetee/lab/v1/",
      inputMode: "audio_replay",
      mcpMode: "selected_device",
      deviceId,
      agent: { id: agentId, version: 7 },
    });
    expect(consumed).toMatchObject({
      tenantId,
      userId,
      agentId,
      configVersion: 7,
      inputMode: "audio_replay",
      mcpMode: "selected_device",
      deviceId,
    });
    await expect(service.consume(String(issued.token))).rejects.toBeInstanceOf(
      UnauthorizedException,
    );
    expect(audit.record).toHaveBeenCalledWith(
      expect.objectContaining({ action: "lab.session.create", targetId: agentId }),
    );
  });
});
