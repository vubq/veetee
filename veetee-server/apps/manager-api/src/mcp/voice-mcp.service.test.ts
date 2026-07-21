import {
  BadGatewayException,
  ServiceUnavailableException,
} from "@nestjs/common";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceMcpService } from "./voice-mcp.service.js";

const tool = {
  name: "self.get_system_info",
  description: "Read diagnostic system information.",
  inputSchema: { type: "object", properties: {}, additionalProperties: false },
  audience: "user",
  safetyClass: "read_only",
  requiresConfirmation: true,
};

describe("VoiceMcpService", () => {
  beforeEach(() => {
    process.env.VEETEE_VOICE_INTERNAL_URL = "http://127.0.0.1:8000";
    process.env.VEETEE_INTERNAL_SERVICE_TOKEN = "internal-test-service-token";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.VEETEE_VOICE_INTERNAL_URL;
    delete process.env.VEETEE_INTERNAL_SERVICE_TOKEN;
  });

  it("loads and validates the live device catalog with service authentication", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([tool]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(new VoiceMcpService().listTools("device/one")).resolves.toEqual([tool]);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/internal/v1/devices/device%2Fone/mcp/tools",
      expect.objectContaining({
        method: "GET",
        headers: expect.any(Headers),
      }),
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer internal-test-service-token");
  });

  it("maps Manager calls to the snake_case voice-server contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ tool: tool.name, result: { isError: false } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await new VoiceMcpService().callTool("device-1", tool.name, {}, true, 2.5);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      arguments: {},
      confirmed: true,
      timeout_seconds: 2.5,
    });
  });

  it("rejects malformed catalogs and unsafe internal base URLs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("not-json", { status: 200, headers: { "content-type": "text/plain" } }),
      ),
    );
    await expect(new VoiceMcpService().listTools("device-1")).rejects.toBeInstanceOf(
      BadGatewayException,
    );

    process.env.VEETEE_VOICE_INTERNAL_URL = "http://192.168.1.20:8000";
    await expect(new VoiceMcpService().listTools("device-1")).rejects.toBeInstanceOf(
      ServiceUnavailableException,
    );
  });
});
