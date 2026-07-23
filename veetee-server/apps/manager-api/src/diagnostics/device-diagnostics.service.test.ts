import { BadGatewayException } from "@nestjs/common";
import { describe, expect, it, vi } from "vitest";

import { DeviceDiagnosticsService } from "./device-diagnostics.service.js";

function counters() {
  return {
    mic_frames: 10,
    mic_samples: 3_200,
    mic_read_errors: 0,
    mic_read_timeouts: 1,
    detector_frame_drops: 0,
    opus_encode_failures: 0,
    uplink_drops: 0,
    playback_queue_drops: 0,
    playback_queue_high_water: 2,
    opus_decode_failures: 0,
    speaker_write_failures: 0,
  };
}

function diagnostic() {
  return {
    state: "completed",
    session_id: 4,
    duration_seconds: 3,
    started_ms: 100,
    ends_ms: 3_100,
    pcm_frames: 20,
    sample_count: 6_400,
    rms: 812.5,
    peak_absolute: 2_100,
    dc_offset: -1.2,
    clipped_samples: 0,
    clipping_percent: 0,
    raw_audio_stored: false,
    counters: counters(),
  };
}

function health() {
  return {
    schema_version: 1,
    device: {
      board: "veetee-s3-n16r8",
      firmware_version: "0.3.0",
      state: "listening",
      assistant_gate_open: true,
      uptime_ms: 100,
      reset_reason: "software",
    },
    memory: {
      internal_free_bytes: 80_000,
      internal_min_free_bytes: 60_000,
      psram_free_bytes: 4_000_000,
      psram_min_free_bytes: 3_500_000,
    },
    network: {
      connected: true,
      rssi: -48,
      ipv4: "192.168.1.44",
      disconnect_count: 2,
      reconnect_attempt_count: 3,
      last_disconnect_reason: 201,
    },
    audio: {
      capture_task_running: true,
      playback_task_running: true,
      lifetime: counters(),
      diagnostic: diagnostic(),
    },
    resources: {
      wake_resource_healthy: true,
      ui_pack_healthy: true,
      wake_dropped_frames: 1,
    },
    tasks: {
      minimum_stack_free_bytes: 2_048,
      capture: {
        expected: true,
        running: true,
        stack_free_bytes: 4_096,
      },
      playback: {
        expected: true,
        running: true,
        stack_free_bytes: 5_120,
      },
      wake: {
        expected: true,
        running: true,
        stack_free_bytes: 3_072,
      },
      websocket_control: {
        expected: true,
        running: true,
        stack_free_bytes: 6_144,
      },
    },
  };
}

function toolPayload(tool: string, value: unknown) {
  return {
    tool,
    result: {
      isError: false,
      content: [{ type: "text", text: JSON.stringify(value) }],
    },
  };
}

describe("DeviceDiagnosticsService", () => {
  it("normalizes the structured health result and keeps privacy false", async () => {
    const voice = {
      callTool: vi.fn().mockResolvedValue(
        toolPayload("self.diagnostics.get_health", health()),
      ),
    };
    const service = new DeviceDiagnosticsService(voice as never);
    const result = await service.health("device-1");
    expect(result.schemaVersion).toBe(1);
    expect(result.network.ipv4).toBe("192.168.1.44");
    expect(result.audio.diagnostic.rawAudioStored).toBe(false);
    expect(result.tasks).toMatchObject({
      minimumStackFreeBytes: 2_048,
      capture: { running: true, stackFreeBytes: 4_096 },
      websocketControl: { running: true, stackFreeBytes: 6_144 },
    });
    expect(voice.callTool).toHaveBeenCalledWith(
      "device-1",
      "self.diagnostics.get_health",
      {},
      true,
      8,
    );
  });

  it("rejects malformed or unbounded diagnostic payloads", async () => {
    const oversized = health();
    oversized.audio.diagnostic.rms = 99_999;
    const voice = {
      callTool: vi.fn().mockResolvedValue(
        toolPayload("self.diagnostics.get_health", oversized),
      ),
    };
    const service = new DeviceDiagnosticsService(voice as never);
    await expect(service.health("device-1")).rejects.toBeInstanceOf(
      BadGatewayException,
    );
  });

  it("rejects unbounded or inconsistent task headroom", async () => {
    const unbounded = health();
    unbounded.tasks.capture.stack_free_bytes = 1_048_577;
    const voice = {
      callTool: vi.fn().mockResolvedValue(
        toolPayload("self.diagnostics.get_health", unbounded),
      ),
    };
    const service = new DeviceDiagnosticsService(voice as never);
    await expect(service.health("device-1")).rejects.toBeInstanceOf(
      BadGatewayException,
    );

    const inconsistent = health();
    inconsistent.tasks.wake.running = false;
    const secondVoice = {
      callTool: vi.fn().mockResolvedValue(
        toolPayload("self.diagnostics.get_health", inconsistent),
      ),
    };
    const secondService = new DeviceDiagnosticsService(secondVoice as never);
    await expect(secondService.health("device-1")).rejects.toBeInstanceOf(
      BadGatewayException,
    );
  });

  it("accepts health from an older schema-v1 device without task metrics", async () => {
    const legacy = health();
    const { tasks: _tasks, ...legacyHealth } = legacy;
    const voice = {
      callTool: vi.fn().mockResolvedValue(
        toolPayload("self.diagnostics.get_health", legacyHealth),
      ),
    };
    const service = new DeviceDiagnosticsService(voice as never);
    await expect(service.health("device-1")).resolves.toMatchObject({
      schemaVersion: 1,
    });
    await expect(service.health("device-1")).resolves.not.toHaveProperty("tasks");
  });

  it("parses bounded audio start and self-test results", async () => {
    const voice = {
      callTool: vi
        .fn()
        .mockResolvedValueOnce(
          toolPayload("self.diagnostics.audio.start", diagnostic()),
        )
        .mockResolvedValueOnce(
          toolPayload("self.diagnostics.run_self_test", {
            schema_version: 1,
            run_at_uptime_ms: 100,
            overall: "pass",
            checks: [
              {
                id: "wifi_connected",
                status: "pass",
                detail: "Connected",
                requires_listener: false,
              },
            ],
          }),
        ),
    };
    const service = new DeviceDiagnosticsService(voice as never);
    await expect(service.startAudio("device-1", 3)).resolves.toMatchObject({
      state: "completed",
      durationSeconds: 3,
    });
    await expect(service.selfTest("device-1")).resolves.toMatchObject({
      overall: "pass",
      checks: [{ id: "wifi_connected" }],
    });
  });
});
