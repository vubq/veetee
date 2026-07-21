import { BadRequestException } from "@nestjs/common";
import type { FastifyReply } from "fastify";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ControlPlaneStore } from "../store/control-plane.store.js";
import { OtaController } from "./ota.controller.js";

const headers = {
  "device-id": "28:84:85:50:9d:1c",
  "client-id": "e7d8d143-667c-43b9-b003-f08c1063516b",
  "device-model": "veetee-s3-n16r8",
  "firmware-version": "0.1.0",
  "accept-language": "vi-VN",
};

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("OtaController", () => {
  it("returns a Xiaozhi-compatible unbound bootstrap response", async () => {
    const store = {
      bootstrapDevice: vi.fn().mockResolvedValue({
        state: "unbound",
        activation: {
          code: "482913",
          challenge: "c3d2a1f0-8b7e-4d6c-9a10-1234567890ab",
          expiresAt: new Date(Date.now() + 600_000).toISOString(),
        },
      }),
    } as unknown as ControlPlaneStore;
    vi.stubEnv("VEETEE_VOICE_WS_URL", "ws://192.168.1.20:8000/veetee/v1/");

    const response = await new OtaController(store).bootstrap(headers, {});

    expect(response.activation).toMatchObject({ code: "482913", message: "482913" });
    expect(response.websocket).toEqual({
      url: "ws://192.168.1.20:8000/veetee/v1/",
      token: "",
    });
    expect(store.bootstrapDevice).toHaveBeenCalledWith(
      "28:84:85:50:9d:1c",
      undefined,
      "0.1.0",
    );
  });

  it("returns authenticated config without another activation code", async () => {
    const store = {
      bootstrapDevice: vi.fn().mockResolvedValue({
        state: "active",
        deviceId: "01JDEVICE",
        agentId: "01JAGENT",
        configVersion: 3,
        resourceVersion: "1.2.0",
      }),
    } as unknown as ControlPlaneStore;
    vi.stubEnv("VEETEE_MANAGER_PUBLIC_URL", "http://192.168.1.20:8001");
    vi.stubEnv("VEETEE_VOICE_WS_URL", "ws://192.168.1.20:8000/veetee/v1/");
    vi.stubEnv(
      "VEETEE_RESOURCE_MANIFEST_URL",
      "http://192.168.1.20:8003/veetee/artifacts/manifests/stable",
    );
    const token = "a".repeat(43);

    const response = await new OtaController(store).bootstrap(
      { ...headers, authorization: `Bearer ${token}` },
      {},
    );

    expect(response.activation).toBeUndefined();
    expect(response.websocket.token).toBe(token);
    expect(response.config).toEqual({
      version: 3,
      etag: "agent-config-3",
      url: "http://192.168.1.20:8001/veetee/config/v1/devices/01JDEVICE",
    });
    expect(response.resources?.version).toBe("1.2.0");
  });

  it("keeps activation polling pending until the device is bound", async () => {
    const store = {
      activateDevice: vi.fn().mockResolvedValue(null),
    } as unknown as ControlPlaneStore;
    let status = 200;
    const reply = {
      code: vi.fn((value: number) => {
        status = value;
      }),
    } as unknown as FastifyReply;

    const response = await new OtaController(store).activate(
      headers,
      { challenge: "challenge-with-enough-entropy" },
      reply,
    );

    expect(status).toBe(202);
    expect(response).toEqual({ status: "pending" });
  });

  it("rejects a body identity that differs from Device-Id", async () => {
    const store = {} as ControlPlaneStore;
    const reply = {} as FastifyReply;
    await expect(
      new OtaController(store).activate(
        headers,
        { hardwareId: "different-device", challenge: "challenge-with-enough-entropy" },
        reply,
      ),
    ).rejects.toBeInstanceOf(BadRequestException);
  });
});
