import { z } from "zod";

const jsonObject = z.record(z.string(), z.unknown());

export const deviceCapabilitiesSchema = z.object({
  board: z.string(),
  display: z.object({
    target: z.string(),
    controller: z.string(),
    width: z.number().int().positive(),
    height: z.number().int().positive(),
    colorFormat: z.string(),
    resourceAbi: z.number().int().positive(),
    uiAbi: z.number().int().positive(),
    slotBytes: z.number().int().positive(),
    hotReload: z.boolean(),
    compositions: z.array(z.enum(["signal", "monolith", "quiet"])),
  }),
  wake: z.object({
    runtime: z.string(),
    runtimeAbi: z.number().int().positive(),
    resourceAbi: z.number().int().positive(),
    slotBytes: z.number().int().positive(),
    sampleRateHz: z.number().int().positive(),
    channels: z.number().int().positive(),
    hotReload: z.boolean(),
  }),
});

export const principalSchema = z.object({
  userId: z.string(),
  tenantId: z.string(),
  tenantSlug: z.string(),
  role: z.enum(["OWNER", "ADMIN", "OPERATOR", "VIEWER"]),
  email: z.string().email(),
  displayName: z.string(),
});

export const tokenResponseSchema = z.object({
  accessToken: z.string(),
  tokenType: z.literal("Bearer"),
  expiresIn: z.number().int().positive(),
  principal: principalSchema,
});

export const deviceSchema = z.object({
  id: z.string(),
  hardwareId: z.string(),
  name: z.string(),
  status: z.enum(["online", "idle", "offline"]),
  agentId: z.string().optional(),
  firmwareVersion: z.string().optional(),
  desiredState: z.object({ version: z.number().int().nonnegative(), state: jsonObject }),
  reportedState: z.object({
    version: z.number().int().nonnegative(),
    state: jsonObject,
    bootId: z.string().optional(),
  }),
  pairedAt: z.string(),
  lastSeenAt: z.string().optional(),
});

export const agentSchema = z.object({
  id: z.string(),
  name: z.string(),
  defaultLocale: z.string(),
  interactionMode: z.enum(["auto", "manual", "realtime"]),
  persona: z.string(),
  draftConfig: jsonObject,
  version: z.number().int().positive(),
  publishedVersion: z.number().int().nonnegative(),
});

export const providerSchema = z.object({
  id: z.string(),
  kind: z.enum(["vad", "asr", "llm", "tts", "realtime", "memory"]),
  adapter: z.string(),
  model: z.string(),
  baseUrl: z.string().optional(),
  secretConfigured: z.boolean(),
  enabled: z.boolean(),
  priority: z.number().int().min(0).max(1_000),
  locales: z.array(z.string()),
  health: z.enum(["unknown", "healthy", "degraded"]),
  healthLatencyMs: z.number().int().nonnegative().optional(),
  healthErrorCode: z.string().optional(),
  healthCheckedAt: z.string().optional(),
  circuitState: z.enum(["closed", "open", "half_open"]),
  failureCount: z.number().int().nonnegative(),
});

export const mcpToolSchema = z.object({
  name: z.string(),
  description: z.string(),
  inputSchema: jsonObject,
  audience: z.enum(["regular", "user"]),
  safetyClass: z
    .enum(["read_only", "reversible", "disruptive", "destructive"])
    .default("read_only"),
  requiresConfirmation: z.boolean().default(false),
});

const audioCountersSchema = z.object({
  micFrames: z.number().int().nonnegative(),
  micSamples: z.number().int().nonnegative(),
  micReadErrors: z.number().int().nonnegative(),
  micReadTimeouts: z.number().int().nonnegative(),
  detectorFrameDrops: z.number().int().nonnegative(),
  opusEncodeFailures: z.number().int().nonnegative(),
  uplinkDrops: z.number().int().nonnegative(),
  playbackQueueDrops: z.number().int().nonnegative(),
  playbackQueueHighWater: z.number().int().min(0).max(1024),
  opusDecodeFailures: z.number().int().nonnegative(),
  speakerWriteFailures: z.number().int().nonnegative(),
});

export const audioDiagnosticSessionSchema = z.object({
  state: z.enum(["not_run", "running", "completed"]),
  sessionId: z.number().int().nonnegative(),
  durationSeconds: z.number().int().min(0).max(30),
  startedMs: z.number().int().nonnegative(),
  endsMs: z.number().int().nonnegative(),
  pcmFrames: z.number().int().nonnegative(),
  sampleCount: z.number().int().nonnegative(),
  rms: z.number().min(0).max(32768),
  peakAbsolute: z.number().int().min(0).max(32768),
  dcOffset: z.number().min(-32768).max(32767),
  clippedSamples: z.number().int().nonnegative(),
  clippingPercent: z.number().min(0).max(100),
  rawAudioStored: z.literal(false),
  counters: audioCountersSchema,
});

export const deviceHealthSchema = z.object({
  schemaVersion: z.literal(1),
  device: z.object({
    board: z.string(),
    firmwareVersion: z.string(),
    state: z.string(),
    assistantGateOpen: z.boolean(),
    uptimeMs: z.number().int().nonnegative(),
    resetReason: z.string(),
  }),
  memory: z.object({
    internalFreeBytes: z.number().int().nonnegative(),
    internalMinFreeBytes: z.number().int().nonnegative(),
    psramFreeBytes: z.number().int().nonnegative(),
    psramMinFreeBytes: z.number().int().nonnegative(),
  }),
  network: z.object({
    connected: z.boolean(),
    rssi: z.number().int().min(-127).max(0),
    ipv4: z.string().max(45),
    disconnectCount: z.number().int().nonnegative(),
    reconnectAttemptCount: z.number().int().nonnegative(),
    lastDisconnectReason: z.number().int().nonnegative(),
  }),
  audio: z.object({
    captureTaskRunning: z.boolean(),
    playbackTaskRunning: z.boolean(),
    lifetime: audioCountersSchema,
    diagnostic: audioDiagnosticSessionSchema,
  }),
  resources: z.object({
    wakeResourceHealthy: z.boolean(),
    uiPackHealthy: z.boolean(),
    wakeDroppedFrames: z.number().int().nonnegative(),
  }),
});

export const deviceSelfTestSchema = z.object({
  schemaVersion: z.literal(1),
  runAtUptimeMs: z.number().int().nonnegative(),
  overall: z.enum(["pass", "fail"]),
  checks: z.array(z.object({
    id: z.string(),
    status: z.enum(["pass", "fail", "not_run"]),
    detail: z.string(),
    requiresListener: z.boolean(),
  })).min(1).max(16),
});

export const conversationEventSchema = z.object({
  id: z.string().uuid(),
  deviceId: z.string().uuid(),
  agentId: z.string().uuid().optional(),
  sessionId: z.string(),
  turnId: z.string().optional(),
  generation: z.number().int().nonnegative(),
  eventType: z.string(),
  payload: jsonObject,
  occurredAt: z.string(),
});

export const auditEventSchema = z.object({
  id: z.string().uuid(),
  action: z.string(),
  targetType: z.string(),
  targetId: z.string(),
  requestId: z.string(),
  beforeHash: z.string().optional(),
  afterHash: z.string().optional(),
  details: jsonObject,
  actorName: z.string().optional(),
  createdAt: z.string(),
});

export const operationsProfileSchema = z.object({
  deployment: z.object({
    mode: z.literal("single_node"),
    domainRequired: z.literal(false),
    managerApiUrl: z.string(),
    voiceWebsocketUrl: z.string(),
  }),
  privacy: z.object({
    rawAudioStored: z.literal(false),
    transcriptStored: z.literal(false),
    conversationEventRetentionDays: z.number().int().min(1).max(30),
  }),
  security: z.object({
    deviceScopedTokens: z.literal(true),
    signedArtifacts: z.literal(true),
    publicTlsRequired: z.literal(false),
  }),
  firmware: z.object({
    configuredVersion: z.string(),
    releaseConfigured: z.boolean(),
    otaRoute: z.literal("/veetee/ota/"),
  }),
});

const detectorProfileSchema = z.object({
  detectorId: z.string(),
  sensitivity: z.number().min(0).max(1),
  cooldownMs: z.number().int().nonnegative(),
  allowedStates: z.array(z.string()),
});

export const artifactSchema = z.object({
  id: z.string(),
  kind: z.enum([
    "resource_bundle",
    "model_pack",
    "display_assets",
    "audio_assets",
    "admission_model",
  ]),
  version: z.string(),
  channel: z.string(),
  sizeBytes: z.number().int().positive(),
  sha256: z.string(),
  contentType: z.string(),
  runtime: z.string(),
  runtimeAbi: z.number().int().positive(),
  license: z.string(),
  board: z.string(),
  minFirmware: z.string(),
  maxFirmware: z.string(),
  signatureKeyId: z.string(),
  securityEpoch: z.number().int().positive(),
  benchmarkStatus: z.enum(["not_run", "passed", "failed"]),
  status: z.enum(["validated", "published", "revoked"]),
  publishedAt: z.string().optional(),
  createdAt: z.string(),
});

export const wakeProfileSchema = z.object({
  id: z.string().uuid(),
  artifactId: z.string(),
  name: z.string(),
  locale: z.string(),
  channel: z.string(),
  activationPhrase: z.string(),
  activation: detectorProfileSchema,
  interrupt: detectorProfileSchema,
  version: z.number().int().positive(),
  publishedVersion: z.number().int().nonnegative(),
  productReady: z.boolean(),
});

export const resourceRolloutSchema = z.object({
  id: z.string().uuid(),
  deviceId: z.string().uuid(),
  artifactId: z.string(),
  wakeProfileVersion: z.number().int().positive(),
  status: z.enum(["active", "complete", "failed", "rolled_back"]),
  desiredStateVersion: z.number().int().positive(),
  createdAt: z.string(),
});

export const uiPackRolloutSchema = z.object({
  id: z.string().uuid(),
  deviceId: z.string().uuid(),
  artifactId: z.string(),
  status: z.enum(["active", "complete", "failed", "rolled_back"]),
  desiredStateVersion: z.number().int().positive(),
  createdAt: z.string(),
});

export const healthSchema = z.object({
  status: z.string(),
  service: z.string().optional(),
  components: z.record(z.string(), z.string()).optional(),
});

export const labSessionSchema = z.object({
  id: z.string().uuid(),
  token: z.string(),
  websocketUrl: z.string(),
  expiresAt: z.string(),
  agent: z.object({
    id: z.string().uuid(),
    name: z.string(),
    locale: z.string(),
    version: z.number().int().positive(),
    interactionMode: z.enum(["auto", "manual", "realtime"]),
  }),
  inputMode: z.enum(["text", "audio_replay", "live_mic"]),
  mcpMode: z.enum(["simulated", "selected_device", "disabled"]),
  deviceId: z.string().uuid().optional(),
});

export const apiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.unknown().optional(),
  request_id: z.string().optional(),
});

export type Principal = z.infer<typeof principalSchema>;
export type Device = z.infer<typeof deviceSchema>;
export type Agent = z.infer<typeof agentSchema>;
export type Provider = z.infer<typeof providerSchema>;
export type McpTool = z.infer<typeof mcpToolSchema>;
export type AudioDiagnosticSession = z.infer<typeof audioDiagnosticSessionSchema>;
export type DeviceHealth = z.infer<typeof deviceHealthSchema>;
export type DeviceSelfTest = z.infer<typeof deviceSelfTestSchema>;
export type ConversationEvent = z.infer<typeof conversationEventSchema>;
export type AuditEvent = z.infer<typeof auditEventSchema>;
export type OperationsProfile = z.infer<typeof operationsProfileSchema>;
export type Artifact = z.infer<typeof artifactSchema>;
export type WakeProfile = z.infer<typeof wakeProfileSchema>;
export type ResourceRollout = z.infer<typeof resourceRolloutSchema>;
export type UiPackRollout = z.infer<typeof uiPackRolloutSchema>;
export type LabSession = z.infer<typeof labSessionSchema>;
export type DeviceCapabilities = z.infer<typeof deviceCapabilitiesSchema>;
