import { z } from "zod";

import {
  agentSchema,
  apiErrorSchema,
  conversationEventSchema,
  deviceSchema,
  healthSchema,
  mcpToolSchema,
  principalSchema,
  providerSchema,
  tokenResponseSchema,
  type Agent,
} from "./schemas";

const apiBaseUrl = (import.meta.env.VITE_MANAGER_API_URL ?? "http://127.0.0.1:8001").replace(
  /\/$/,
  "",
);
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
  conversationEvents: (deviceId: string, limit = 100) =>
    request(
      `/api/v1/conversation-events?deviceId=${encodeURIComponent(deviceId)}&limit=${limit}`,
      z.array(conversationEventSchema),
    ),

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
