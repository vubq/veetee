import { describe, expect, it, vi } from "vitest";

import { probeVoiceRuntimeComponent } from "./voice-runtime-probe.js";

describe("probeVoiceRuntimeComponent", () => {
  it("reads the requested in-process component from Voice Server readiness", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({
        status: "ready",
        components: [
          { name: "vad", healthy: true, required: true },
          { name: "asr", healthy: true, required: true },
          { name: "tts", healthy: true, required: true },
        ],
      }), { status: 200, headers: { "content-type": "application/json" } }),
    );

    await expect(probeVoiceRuntimeComponent("asr", "http://127.0.0.1:8000/", fetchImpl))
      .resolves.toEqual({ healthy: true, errorCode: null });
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/health/ready",
      expect.objectContaining({ headers: { accept: "application/json" } }),
    );
  });

  it("does not fail a healthy component because another component makes readiness return 503", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({
        status: "not_ready",
        components: [
          { name: "asr", healthy: true, required: true },
          { name: "llm", healthy: false, required: true },
        ],
      }), { status: 503, headers: { "content-type": "application/json" } }),
    );

    await expect(probeVoiceRuntimeComponent("asr", "http://voice.local", fetchImpl))
      .resolves.toEqual({ healthy: true, errorCode: null });
  });

  it("reports a missing component instead of claiming the runtime is healthy", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ready", components: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(probeVoiceRuntimeComponent("tts", "http://voice.local", fetchImpl))
      .resolves.toEqual({ healthy: false, errorCode: "runtime_component_unreported" });
  });

  it("maps connection failures to a stable operator-facing error code", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("connection refused"));

    await expect(probeVoiceRuntimeComponent("vad", "http://voice.local", fetchImpl))
      .resolves.toEqual({ healthy: false, errorCode: "voice_runtime_unreachable" });
  });
});
