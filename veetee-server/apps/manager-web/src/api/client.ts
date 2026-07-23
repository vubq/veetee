import { z } from "zod";

import {
  agentSchema,
  auditEventSchema,
  apiErrorSchema,
  artifactSchema,
  conversationEventSchema,
  deviceHealthSchema,
  deviceSelfTestSchema,
  audioDiagnosticSessionSchema,
  deviceSchema,
  healthSchema,
  labSessionSchema,
  mcpToolSchema,
  principalSchema,
  providerSchema,
  operationsProfileSchema,
  resourceRolloutSchema,
  tokenResponseSchema,
  uiPackRolloutSchema,
  wakeProfileSchema,
  type Agent,
  type Provider,
} from "./schemas";
import { resolveManagerApiBaseUrl } from "./base-url";

const apiBaseUrl = resolveManagerApiBaseUrl(import.meta.env.VITE_MANAGER_API_URL);
const tokenStorageKey = "veetee.manager.access-token";

let accessToken = sessionStorage.getItem(tokenStorageKey) ?? "";
let refreshPromise: Promise<boolean> | null = null;
let unauthorizedHandler: (() => void) | undefined;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
  ) {
    super(message);
  }
}

function setAccessToken(token: string): void {
  accessToken = token;
  if (token) sessionStorage.setItem(tokenStorageKey, token);
  else sessionStorage.removeItem(tokenStorageKey);
}

async function parseError(response: Response): Promise<ApiError> {
  const payload = apiErrorSchema.safeParse(await response.json().catch(() => ({})));
  if (payload.success) {
    return new ApiError(
      payload.data.message,
      response.status,
      payload.data.code,
      payload.data.request_id,
    );
  }
  return new ApiError(`Manager API returned ${response.status}`, response.status, "request_failed");
}

async function rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);
  return fetch(`${apiBaseUrl}${path}`, { ...init, headers, credentials: "include" });
}

async function refreshSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    const response = await fetch(`${apiBaseUrl}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) {
      setAccessToken("");
      unauthorizedHandler?.();
      return false;
    }
    const pair = tokenResponseSchema.parse(await response.json());
    setAccessToken(pair.accessToken);
    return true;
  })().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  let response = await rawRequest(path, init);
  if (response.status === 401 && retry && (await refreshSession())) {
    response = await rawRequest(path, init);
  }
  if (!response.ok) throw await parseError(response);
  return schema.parse(await response.json());
}

export const managerApi = {
  baseUrl: apiBaseUrl,

  setUnauthorizedHandler(handler: () => void): void {
    unauthorizedHandler = handler;
  },

  hasAccessToken(): boolean {
    return Boolean(accessToken);
  },

  clearAccessToken(): void {
    setAccessToken("");
  },

  async login(email: string, password: string, tenantSlug?: string) {
    const response = await fetch(`${apiBaseUrl}/api/v1/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password, ...(tenantSlug ? { tenantSlug } : {}) }),
    });
    if (!response.ok) throw await parseError(response);
    const pair = tokenResponseSchema.parse(await response.json());
    setAccessToken(pair.accessToken);
    return pair;
  },

  async refresh() {
    if (!(await refreshSession())) throw new ApiError("Phiên đăng nhập đã hết hạn", 401, "unauthorized");
    return request("/api/v1/auth/me", principalSchema, {}, false);
  },

  me: () => request("/api/v1/auth/me", principalSchema),
  health: () => request("/health/ready", healthSchema, {}, false),
  devices: () => request("/api/v1/devices", z.array(deviceSchema)),
  agents: () => request("/api/v1/agents", z.array(agentSchema)),
  providers: () => request("/api/v1/providers", z.array(providerSchema)),
  mcpTools: () => request("/api/v1/mcp/tools", z.array(mcpToolSchema)),
  deviceMcpTools: (deviceId: string) =>
    request(
      `/api/v1/devices/${encodeURIComponent(deviceId)}/mcp/tools`,
      z.array(mcpToolSchema),
    ),
  deviceDiagnosticsHealth: (deviceId: string) =>
    request(
      `/api/v1/devices/${encodeURIComponent(deviceId)}/diagnostics/health`,
      deviceHealthSchema,
    ),
  startDeviceAudioDiagnostic: (deviceId: string, durationSeconds: number) =>
    request(
      `/api/v1/devices/${encodeURIComponent(deviceId)}/diagnostics/audio-sessions`,
      audioDiagnosticSessionSchema,
      {
        method: "POST",
        body: JSON.stringify({ durationSeconds }),
      },
    ),
  runDeviceSelfTest: (deviceId: string) =>
    request(
      `/api/v1/devices/${encodeURIComponent(deviceId)}/diagnostics/self-test`,
      deviceSelfTestSchema,
      { method: "POST" },
    ),
  conversationEvents: (deviceId?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (deviceId) params.set("deviceId", deviceId);
    return request(`/api/v1/conversation-events?${params}`, z.array(conversationEventSchema));
  },
  auditEvents: (input: { limit?: number; action?: string; targetType?: string } = {}) => {
    const params = new URLSearchParams();
    if (input.limit) params.set("limit", String(input.limit));
    if (input.action) params.set("action", input.action);
    if (input.targetType) params.set("targetType", input.targetType);
    const query = params.toString();
    return request(`/api/v1/audit-events${query ? `?${query}` : ""}`, z.array(auditEventSchema));
  },
  operationsProfile: () => request("/api/v1/operations/profile", operationsProfileSchema),
  artifacts: () => request("/api/v1/artifacts", z.array(artifactSchema)),
  wakeProfiles: () => request("/api/v1/wake-profiles", z.array(wakeProfileSchema)),
  resourceRollouts: () =>
    request("/api/v1/resource-rollouts", z.array(resourceRolloutSchema)),
  uiPackRollouts: () =>
    request("/api/v1/ui-packs/rollouts", z.array(uiPackRolloutSchema)),

  createLabSession(input: {
    agentId: string;
    inputMode: "text" | "audio_replay" | "live_mic";
    mcpMode: "simulated" | "selected_device" | "disabled";
    deviceId?: string;
  }) {
    return request("/api/v1/lab/sessions", labSessionSchema, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  createAgent(input: {
    name: string;
    defaultLocale: string;
    interactionMode: "auto" | "manual" | "realtime";
    persona: string;
    draftConfig?: Record<string, unknown>;
  }) {
    return request("/api/v1/agents", agentSchema, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  async logout(): Promise<void> {
    const response = await rawRequest("/api/v1/auth/logout", { method: "POST" });
    setAccessToken("");
    if (!response.ok && response.status !== 401) throw await parseError(response);
  },

  claimPairing(code: string, name: string, agentId?: string) {
    return request(`/api/v1/devices/activation/${encodeURIComponent(code)}/bind`, deviceSchema, {
      method: "POST",
      body: JSON.stringify({ name, ...(agentId ? { agentId } : {}) }),
    });
  },

  assignDeviceAgent(deviceId: string, agentId?: string) {
    return request(`/api/v1/devices/${encodeURIComponent(deviceId)}/agent`, deviceSchema, {
      method: "PUT",
      body: JSON.stringify(agentId ? { agentId } : {}),
    });
  },

  updateAgent(id: string, input: Partial<Pick<Agent, "name" | "defaultLocale" | "interactionMode" | "persona" | "draftConfig">>) {
    return request(`/api/v1/agents/${encodeURIComponent(id)}`, agentSchema, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },

  publishAgent(id: string) {
    return request(`/api/v1/agents/${encodeURIComponent(id)}/publish`, agentSchema, {
      method: "POST",
    });
  },

  testProvider(id: string) {
    return request(`/api/v1/providers/${encodeURIComponent(id)}/test`, providerSchema, {
      method: "POST",
    });
  },

  updateProvider(
    id: string,
    input: Partial<
      Pick<Provider, "adapter" | "model" | "enabled" | "priority" | "locales">
    > & {
      baseUrl?: string | null;
      secretAction?: "keep" | "rotate" | "clear";
      secret?: string;
    },
  ) {
    return request(`/api/v1/providers/${encodeURIComponent(id)}`, providerSchema, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },

  registerArtifact(artifactId: string, license: string) {
    return request("/api/v1/artifacts/register", artifactSchema, {
      method: "POST",
      body: JSON.stringify({ artifactId, license, benchmarkStatus: "not_run" }),
    });
  },

  publishArtifact(id: string) {
    return request(`/api/v1/artifacts/${encodeURIComponent(id)}/publish`, artifactSchema, {
      method: "POST",
    });
  },

  stageUiPack(file: File) {
    return request("/api/v1/ui-packs/uploads", artifactSchema, {
      method: "POST",
      headers: {
        "content-type": "application/vnd.veetee.ui-pack",
        "x-veetee-file-name": file.name,
      },
      body: file,
    });
  },

  stageStandardUiPack(theme: "signal" | "monolith" | "quiet") {
    return request(
      `/api/v1/ui-packs/standard/${encodeURIComponent(theme)}/stage`,
      artifactSchema,
      { method: "POST" },
    );
  },

  rolloutUiPack(id: string, deviceIds: string[]) {
    return request(
      `/api/v1/ui-packs/${encodeURIComponent(id)}/rollout`,
      z.array(uiPackRolloutSchema),
      {
        method: "POST",
        body: JSON.stringify({ deviceIds }),
      },
    );
  },

  createWakeProfile(input: {
    artifactId: string;
    name: string;
    locale: string;
    channel: string;
    activationPhrase: string;
    activation: {
      detectorId: string;
      sensitivity: number;
      cooldownMs: number;
      allowedStates: string[];
    };
    interrupt: {
      detectorId: string;
      sensitivity: number;
      cooldownMs: number;
      allowedStates: string[];
    };
  }) {
    return request("/api/v1/wake-profiles", wakeProfileSchema, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },

  publishWakeProfile(id: string) {
    return request(`/api/v1/wake-profiles/${encodeURIComponent(id)}/publish`, wakeProfileSchema, {
      method: "POST",
    });
  },

  rolloutWakeProfile(wakeProfileId: string, deviceIds: string[]) {
    return request("/api/v1/resource-rollouts", z.array(resourceRolloutSchema), {
      method: "POST",
      body: JSON.stringify({ wakeProfileId, deviceIds }),
    });
  },

  callDeviceTool(
    deviceId: string,
    name: string,
    argumentsValue: Record<string, unknown>,
    confirmed: boolean,
  ) {
    return request(
      `/api/v1/devices/${encodeURIComponent(deviceId)}/mcp/tools/${encodeURIComponent(name)}/call`,
      z.record(z.string(), z.unknown()),
      {
        method: "POST",
        body: JSON.stringify({
          arguments: argumentsValue,
          confirmed,
          timeoutSeconds: 10,
        }),
      },
    );
  },
};
