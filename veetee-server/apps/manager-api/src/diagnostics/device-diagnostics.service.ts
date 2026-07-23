import { BadGatewayException, Injectable } from "@nestjs/common";

import { VoiceMcpService } from "../mcp/voice-mcp.service.js";

const maxCounter = Number.MAX_SAFE_INTEGER;

export interface AudioCounters {
  micFrames: number;
  micSamples: number;
  micReadErrors: number;
  micReadTimeouts: number;
  detectorFrameDrops: number;
  opusEncodeFailures: number;
  uplinkDrops: number;
  playbackQueueDrops: number;
  playbackQueueHighWater: number;
  opusDecodeFailures: number;
  speakerWriteFailures: number;
}

export interface AudioDiagnosticSession {
  state: "not_run" | "running" | "completed";
  sessionId: number;
  durationSeconds: number;
  startedMs: number;
  endsMs: number;
  pcmFrames: number;
  sampleCount: number;
  rms: number;
  peakAbsolute: number;
  dcOffset: number;
  clippedSamples: number;
  clippingPercent: number;
  rawAudioStored: false;
  counters: AudioCounters;
}

export interface TaskRuntimeHealth {
  expected: boolean;
  running: boolean;
  stackFreeBytes: number;
}

export interface TaskRuntimeHealthGroup {
  minimumStackFreeBytes: number;
  capture: TaskRuntimeHealth;
  playback: TaskRuntimeHealth;
  wake: TaskRuntimeHealth;
  websocketControl: TaskRuntimeHealth;
}

export interface DeviceHealth {
  schemaVersion: 1;
  device: {
    board: string;
    firmwareVersion: string;
    state: string;
    assistantGateOpen: boolean;
    uptimeMs: number;
    resetReason: string;
  };
  memory: {
    internalFreeBytes: number;
    internalMinFreeBytes: number;
    psramFreeBytes: number;
    psramMinFreeBytes: number;
  };
  network: {
    connected: boolean;
    rssi: number;
    ipv4: string;
    disconnectCount: number;
    reconnectAttemptCount: number;
    lastDisconnectReason: number;
  };
  audio: {
    captureTaskRunning: boolean;
    playbackTaskRunning: boolean;
    lifetime: AudioCounters;
    diagnostic: AudioDiagnosticSession;
  };
  resources: {
    wakeResourceHealthy: boolean;
    uiPackHealthy: boolean;
    wakeDroppedFrames: number;
  };
  tasks?: TaskRuntimeHealthGroup;
}

export interface DeviceSelfTest {
  schemaVersion: 1;
  runAtUptimeMs: number;
  overall: "pass" | "fail";
  checks: Array<{
    id: string;
    status: "pass" | "fail" | "not_run";
    detail: string;
    requiresListener: boolean;
  }>;
}

@Injectable()
export class DeviceDiagnosticsService {
  constructor(private readonly voiceMcp: VoiceMcpService) {}

  async health(deviceId: string): Promise<DeviceHealth> {
    const payload = await this.voiceMcp.callTool(
      deviceId,
      "self.diagnostics.get_health",
      {},
      true,
      8,
    );
    return this.parseHealth(this.parseToolText(payload, "self.diagnostics.get_health"));
  }

  async startAudio(
    deviceId: string,
    durationSeconds: number,
  ): Promise<AudioDiagnosticSession> {
    const payload = await this.voiceMcp.callTool(
      deviceId,
      "self.diagnostics.audio.start",
      { duration_seconds: durationSeconds },
      true,
      8,
    );
    return this.parseAudioDiagnostic(
      this.parseToolText(payload, "self.diagnostics.audio.start"),
    );
  }

  async selfTest(deviceId: string): Promise<DeviceSelfTest> {
    const payload = await this.voiceMcp.callTool(
      deviceId,
      "self.diagnostics.run_self_test",
      {},
      true,
      8,
    );
    return this.parseSelfTest(
      this.parseToolText(payload, "self.diagnostics.run_self_test"),
    );
  }

  private parseToolText(
    payload: Record<string, unknown>,
    expectedTool: string,
  ): unknown {
    if (payload.tool !== expectedTool || !this.isRecord(payload.result)) {
      throw new BadGatewayException("Device diagnostic MCP result is invalid");
    }
    const result = payload.result;
    if (result.isError !== false || !Array.isArray(result.content) || result.content.length !== 1) {
      throw new BadGatewayException("Device diagnostic MCP result is invalid");
    }
    const item = result.content[0];
    if (
      !this.isRecord(item) ||
      item.type !== "text" ||
      typeof item.text !== "string" ||
      item.text.length === 0 ||
      Buffer.byteLength(item.text, "utf8") > 6_144
    ) {
      throw new BadGatewayException("Device diagnostic MCP content is invalid");
    }
    try {
      return JSON.parse(item.text);
    } catch {
      throw new BadGatewayException("Device diagnostic content is not valid JSON");
    }
  }

  private parseHealth(value: unknown): DeviceHealth {
    const root = this.record(value, "health");
    const device = this.record(root.device, "health.device");
    const memory = this.record(root.memory, "health.memory");
    const network = this.record(root.network, "health.network");
    const audio = this.record(root.audio, "health.audio");
    const resources = this.record(root.resources, "health.resources");
    const tasks =
      root.tasks === undefined ? undefined : this.parseTaskHealthGroup(root.tasks);
    if (this.integer(root.schema_version, 1, 1, "schema_version") !== 1) {
      throw new BadGatewayException("Device health schema is unsupported");
    }
    const parsed: DeviceHealth = {
      schemaVersion: 1,
      device: {
        board: this.string(device.board, 1, 64, "device.board"),
        firmwareVersion: this.string(
          device.firmware_version,
          1,
          64,
          "device.firmware_version",
        ),
        state: this.string(device.state, 1, 32, "device.state"),
        assistantGateOpen: this.boolean(
          device.assistant_gate_open,
          "device.assistant_gate_open",
        ),
        uptimeMs: this.integer(device.uptime_ms, 0, maxCounter, "device.uptime_ms"),
        resetReason: this.string(device.reset_reason, 1, 64, "device.reset_reason"),
      },
      memory: {
        internalFreeBytes: this.integer(
          memory.internal_free_bytes,
          0,
          0xffff_ffff,
          "memory.internal_free_bytes",
        ),
        internalMinFreeBytes: this.integer(
          memory.internal_min_free_bytes,
          0,
          0xffff_ffff,
          "memory.internal_min_free_bytes",
        ),
        psramFreeBytes: this.integer(
          memory.psram_free_bytes,
          0,
          0xffff_ffff,
          "memory.psram_free_bytes",
        ),
        psramMinFreeBytes: this.integer(
          memory.psram_min_free_bytes,
          0,
          0xffff_ffff,
          "memory.psram_min_free_bytes",
        ),
      },
      network: {
        connected: this.boolean(network.connected, "network.connected"),
        rssi: this.integer(network.rssi, -127, 0, "network.rssi"),
        ipv4: this.string(network.ipv4, 0, 45, "network.ipv4"),
        disconnectCount: this.integer(
          network.disconnect_count,
          0,
          maxCounter,
          "network.disconnect_count",
        ),
        reconnectAttemptCount: this.integer(
          network.reconnect_attempt_count,
          0,
          maxCounter,
          "network.reconnect_attempt_count",
        ),
        lastDisconnectReason: this.integer(
          network.last_disconnect_reason,
          0,
          65_535,
          "network.last_disconnect_reason",
        ),
      },
      audio: {
        captureTaskRunning: this.boolean(
          audio.capture_task_running,
          "audio.capture_task_running",
        ),
        playbackTaskRunning: this.boolean(
          audio.playback_task_running,
          "audio.playback_task_running",
        ),
        lifetime: this.parseCounters(audio.lifetime, "audio.lifetime"),
        diagnostic: this.parseAudioDiagnostic(audio.diagnostic),
      },
      resources: {
        wakeResourceHealthy: this.boolean(
          resources.wake_resource_healthy,
          "resources.wake_resource_healthy",
        ),
        uiPackHealthy: this.boolean(
          resources.ui_pack_healthy,
          "resources.ui_pack_healthy",
        ),
        wakeDroppedFrames: this.integer(
          resources.wake_dropped_frames,
          0,
          maxCounter,
          "resources.wake_dropped_frames",
        ),
      },
    };
    if (tasks !== undefined) parsed.tasks = tasks;
    return parsed;
  }

  private parseTaskHealthGroup(value: unknown): TaskRuntimeHealthGroup {
    const tasks = this.record(value, "health.tasks");
    return {
      minimumStackFreeBytes: this.integer(
        tasks.minimum_stack_free_bytes,
        256,
        65_536,
        "tasks.minimum_stack_free_bytes",
      ),
      capture: this.parseTaskHealth(tasks.capture, "tasks.capture"),
      playback: this.parseTaskHealth(tasks.playback, "tasks.playback"),
      wake: this.parseTaskHealth(tasks.wake, "tasks.wake"),
      websocketControl: this.parseTaskHealth(
        tasks.websocket_control,
        "tasks.websocket_control",
      ),
    };
  }

  private parseTaskHealth(value: unknown, path: string): TaskRuntimeHealth {
    const task = this.record(value, path);
    const expected = this.boolean(task.expected, `${path}.expected`);
    const running = this.boolean(task.running, `${path}.running`);
    const stackFreeBytes = this.integer(
      task.stack_free_bytes,
      0,
      1_048_576,
      `${path}.stack_free_bytes`,
    );
    if ((!running && stackFreeBytes !== 0) || (!expected && running)) {
      throw new BadGatewayException(`Device diagnostic field ${path} is inconsistent`);
    }
    return { expected, running, stackFreeBytes };
  }

  private parseAudioDiagnostic(value: unknown): AudioDiagnosticSession {
    const session = this.record(value, "audio.diagnostic");
    const state = this.enumValue(
      session.state,
      ["not_run", "running", "completed"] as const,
      "audio.diagnostic.state",
    );
    const sessionId = this.integer(
      session.session_id,
      0,
      0xffff_ffff,
      "audio.diagnostic.session_id",
    );
    const durationSeconds = this.integer(
      session.duration_seconds,
      0,
      30,
      "audio.diagnostic.duration_seconds",
    );
    const startedMs = this.integer(
      session.started_ms,
      0,
      maxCounter,
      "audio.diagnostic.started_ms",
    );
    const endsMs = this.integer(
      session.ends_ms,
      0,
      maxCounter,
      "audio.diagnostic.ends_ms",
    );
    if (
      (state === "not_run" && (sessionId !== 0 || durationSeconds !== 0)) ||
      (state !== "not_run" &&
        (sessionId < 1 || durationSeconds < 1 || endsMs < startedMs))
    ) {
      throw new BadGatewayException("Device audio diagnostic state is inconsistent");
    }
    if (session.raw_audio_stored !== false) {
      throw new BadGatewayException("Device audio diagnostic privacy flag is invalid");
    }
    return {
      state,
      sessionId,
      durationSeconds,
      startedMs,
      endsMs,
      pcmFrames: this.integer(
        session.pcm_frames,
        0,
        maxCounter,
        "audio.diagnostic.pcm_frames",
      ),
      sampleCount: this.integer(
        session.sample_count,
        0,
        maxCounter,
        "audio.diagnostic.sample_count",
      ),
      rms: this.number(session.rms, 0, 32_768, "audio.diagnostic.rms"),
      peakAbsolute: this.integer(
        session.peak_absolute,
        0,
        32_768,
        "audio.diagnostic.peak_absolute",
      ),
      dcOffset: this.number(
        session.dc_offset,
        -32_768,
        32_767,
        "audio.diagnostic.dc_offset",
      ),
      clippedSamples: this.integer(
        session.clipped_samples,
        0,
        maxCounter,
        "audio.diagnostic.clipped_samples",
      ),
      clippingPercent: this.number(
        session.clipping_percent,
        0,
        100,
        "audio.diagnostic.clipping_percent",
      ),
      rawAudioStored: false,
      counters: this.parseCounters(
        session.counters,
        "audio.diagnostic.counters",
      ),
    };
  }

  private parseSelfTest(value: unknown): DeviceSelfTest {
    const root = this.record(value, "self_test");
    if (this.integer(root.schema_version, 1, 1, "schema_version") !== 1) {
      throw new BadGatewayException("Device self-test schema is unsupported");
    }
    if (!Array.isArray(root.checks) || root.checks.length < 1 || root.checks.length > 16) {
      throw new BadGatewayException("Device self-test check list is invalid");
    }
    const seen = new Set<string>();
    const checks = root.checks.map((value, index) => {
      const check = this.record(value, `self_test.checks[${index}]`);
      const id = this.string(check.id, 1, 64, `self_test.checks[${index}].id`);
      if (!/^[a-z][a-z0-9_]*$/.test(id) || seen.has(id)) {
        throw new BadGatewayException("Device self-test check id is invalid");
      }
      seen.add(id);
      return {
        id,
        status: this.enumValue(
          check.status,
          ["pass", "fail", "not_run"] as const,
          `self_test.checks[${index}].status`,
        ),
        detail: this.string(
          check.detail,
          1,
          256,
          `self_test.checks[${index}].detail`,
        ),
        requiresListener: this.boolean(
          check.requires_listener,
          `self_test.checks[${index}].requires_listener`,
        ),
      };
    });
    const overall = this.enumValue(
      root.overall,
      ["pass", "fail"] as const,
      "self_test.overall",
    );
    if (
      (overall === "pass" && checks.some((check) => check.status === "fail")) ||
      (overall === "fail" && !checks.some((check) => check.status === "fail"))
    ) {
      throw new BadGatewayException("Device self-test overall status is inconsistent");
    }
    return {
      schemaVersion: 1,
      runAtUptimeMs: this.integer(
        root.run_at_uptime_ms,
        0,
        maxCounter,
        "self_test.run_at_uptime_ms",
      ),
      overall,
      checks,
    };
  }

  private parseCounters(value: unknown, path: string): AudioCounters {
    const counters = this.record(value, path);
    return {
      micFrames: this.integer(counters.mic_frames, 0, maxCounter, `${path}.mic_frames`),
      micSamples: this.integer(
        counters.mic_samples,
        0,
        maxCounter,
        `${path}.mic_samples`,
      ),
      micReadErrors: this.integer(
        counters.mic_read_errors,
        0,
        maxCounter,
        `${path}.mic_read_errors`,
      ),
      micReadTimeouts: this.integer(
        counters.mic_read_timeouts,
        0,
        maxCounter,
        `${path}.mic_read_timeouts`,
      ),
      detectorFrameDrops: this.integer(
        counters.detector_frame_drops,
        0,
        maxCounter,
        `${path}.detector_frame_drops`,
      ),
      opusEncodeFailures: this.integer(
        counters.opus_encode_failures,
        0,
        maxCounter,
        `${path}.opus_encode_failures`,
      ),
      uplinkDrops: this.integer(
        counters.uplink_drops,
        0,
        maxCounter,
        `${path}.uplink_drops`,
      ),
      playbackQueueDrops: this.integer(
        counters.playback_queue_drops,
        0,
        maxCounter,
        `${path}.playback_queue_drops`,
      ),
      playbackQueueHighWater: this.integer(
        counters.playback_queue_high_water,
        0,
        1_024,
        `${path}.playback_queue_high_water`,
      ),
      opusDecodeFailures: this.integer(
        counters.opus_decode_failures,
        0,
        maxCounter,
        `${path}.opus_decode_failures`,
      ),
      speakerWriteFailures: this.integer(
        counters.speaker_write_failures,
        0,
        maxCounter,
        `${path}.speaker_write_failures`,
      ),
    };
  }

  private record(value: unknown, path: string): Record<string, unknown> {
    if (!this.isRecord(value)) {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value;
  }

  private string(
    value: unknown,
    minimum: number,
    maximum: number,
    path: string,
  ): string {
    if (typeof value !== "string" || value.length < minimum || value.length > maximum) {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value;
  }

  private boolean(value: unknown, path: string): boolean {
    if (typeof value !== "boolean") {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value;
  }

  private integer(
    value: unknown,
    minimum: number,
    maximum: number,
    path: string,
  ): number {
    if (
      typeof value !== "number" ||
      !Number.isSafeInteger(value) ||
      value < minimum ||
      value > maximum
    ) {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value;
  }

  private number(
    value: unknown,
    minimum: number,
    maximum: number,
    path: string,
  ): number {
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < minimum ||
      value > maximum
    ) {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value;
  }

  private enumValue<const T extends readonly string[]>(
    value: unknown,
    accepted: T,
    path: string,
  ): T[number] {
    if (typeof value !== "string" || !accepted.includes(value)) {
      throw new BadGatewayException(`Device diagnostic field ${path} is invalid`);
    }
    return value as T[number];
  }

  private isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }
}
