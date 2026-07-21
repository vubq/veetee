import { z } from "zod";

const jsonObject = z.record(z.string(), z.unknown());

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
  health: z.enum(["unknown", "healthy", "degraded"]),
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

export const healthSchema = z.object({
  status: z.string(),
  service: z.string().optional(),
  components: z.record(z.string(), z.string()).optional(),
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
export type ConversationEvent = z.infer<typeof conversationEventSchema>;
export type Artifact = z.infer<typeof artifactSchema>;
export type WakeProfile = z.infer<typeof wakeProfileSchema>;
export type ResourceRollout = z.infer<typeof resourceRolloutSchema>;
