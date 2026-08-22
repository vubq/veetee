import { reactive } from 'vue'
import type { AgentSummary } from '@/types/agent'

export const authState = reactive({
  authenticated: false,
  userEmail: '',
  loginError: '',
  loggingIn: false,
  logoutWarning: '',
})

type AgentWire = {
  id: string
  name: string
  version: number
  role_prompt: string
  personality: string
  address_style: string
  language: string
  detail_level: string
  response_style: string
  model_id: string
  voice_id: string
  intent_strategy: string
  memory_enabled: boolean
  memory_min_confidence: number
  tool_policy: Record<string, unknown>
  memory_policy: Record<string, unknown>
}

function toAgentSummary(agent: AgentWire): AgentSummary {
  return {
    id: agent.id,
    name: agent.name,
    role: agent.personality || agent.role_prompt,
    model: agent.model_id,
    lastConversation: 'Chưa có dữ liệu',
    deviceCount: 0,
    online: false,
    version: agent.version,
    rolePrompt: agent.role_prompt,
    personality: agent.personality,
    addressStyle: agent.address_style,
    language: agent.language,
    detailLevel: agent.detail_level,
    responseStyle: agent.response_style,
    modelId: agent.model_id,
    voiceId: agent.voice_id,
    intentStrategy: agent.intent_strategy,
    memoryEnabled: agent.memory_enabled,
    memoryMinConfidence: agent.memory_min_confidence,
    toolPolicy: agent.tool_policy,
    memoryPolicy: agent.memory_policy,
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8080'
let accessToken = ''
let sessionGeneration = 0

export class ApiError extends Error {
  readonly name = 'ApiError'

  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
  }
}

function errorMessage(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === 'string') return [item]
      if (item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string') return [item.msg]
      return []
    })
    if (messages.length) return messages.join(' ')
  }
  return `API error ${status}`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const requestToken = accessToken
  const requestGeneration = sessionGeneration
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(requestToken ? { Authorization: `Bearer ${requestToken}` } : {}),
        ...init.headers,
      },
    })
  } catch (reason) {
    throw new ApiError(0, 'Không thể kết nối tới máy chủ.', reason)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: unknown }
    if (
      response.status === 401
      && requestToken
      && requestToken === accessToken
      && requestGeneration === sessionGeneration
    ) {
      accessToken = ''
      sessionGeneration += 1
      authState.authenticated = false
      authState.userEmail = ''
    }
    throw new ApiError(response.status, errorMessage(response.status, body.detail), body.detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function login(email: string, password: string): Promise<void> {
  authState.loggingIn = true
  authState.loginError = ''
  try {
    const response = await request<{ access_token: string }>('/api/v1/control/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    accessToken = response.access_token
    sessionGeneration += 1
    authState.authenticated = true
    authState.userEmail = email
    authState.logoutWarning = ''
  } catch (error) {
    authState.loginError = error instanceof Error ? error.message : 'Đăng nhập thất bại.'
    throw error
  } finally {
    authState.loggingIn = false
  }
}

export async function logout(): Promise<void> {
  let revoked = false
  try {
    if (accessToken) {
      await request('/api/v1/control/auth/logout', { method: 'POST' })
      revoked = true
    }
  } catch {
    authState.logoutWarning = 'Đã đăng xuất trên trình duyệt nhưng chưa xác nhận thu hồi được phiên trên máy chủ. Hãy đăng nhập lại và thử đăng xuất khi kết nối ổn định.'
  } finally {
    accessToken = ''
    sessionGeneration += 1
    authState.authenticated = false
    authState.userEmail = ''
    if (revoked) authState.logoutWarning = ''
  }
}

export function listAgents(): Promise<AgentSummary[]> {
  return request<AgentWire[]>('/api/v1/control/agents').then((agents) => agents.map(toAgentSummary))
}

export function updateAgent(agent: AgentSummary): Promise<AgentSummary> {
  const body = {
    name: agent.name,
    role_prompt: agent.rolePrompt ?? '',
    personality: agent.personality ?? '',
    address_style: agent.addressStyle ?? '',
    language: agent.language ?? 'vi-VN',
    detail_level: agent.detailLevel ?? 'adaptive',
    response_style: agent.responseStyle ?? '',
    model_id: agent.modelId ?? '',
    voice_id: agent.voiceId ?? '',
    intent_strategy: agent.intentStrategy ?? 'function_call',
    memory_enabled: agent.memoryEnabled ?? true,
    memory_min_confidence: agent.memoryMinConfidence ?? 0.8,
    tool_policy: agent.toolPolicy ?? {},
    memory_policy: agent.memoryPolicy ?? {},
    expected_version: agent.version,
  }
  return request<AgentWire>(`/api/v1/control/agents/${agent.id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }).then(toAgentSummary)
}

export function createAgent(name: string, rolePrompt: string): Promise<AgentSummary> {
  return request<AgentWire>('/api/v1/control/agents', {
    method: 'POST',
    body: JSON.stringify({ name, role_prompt: rolePrompt }),
  }).then(toAgentSummary)
}

export function deleteAgent(agentId: string): Promise<void> {
  return request<void>(`/api/v1/control/agents/${encodeURIComponent(agentId)}`, {
    method: 'DELETE',
  })
}

export type MemoryItem = {
  id: string
  agent_id: string | null
  kind: 'working' | 'episodic' | 'profile'
  content: string
  provenance: string
  confidence: number
  metadata?: Record<string, unknown>
}

export function listMemories(agentId?: string): Promise<MemoryItem[]> {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  return request<MemoryItem[]>(`/api/v1/control/memories${suffix}`)
}

export function createMemory(payload: {
  agent_id?: string
  kind: string
  content: string
  provenance: string
  confidence?: number
}): Promise<MemoryItem> {
  return request<MemoryItem>('/api/v1/control/memories', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function forgetMemory(memoryId: string): Promise<void> {
  return request<void>(`/api/v1/control/memories/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
  })
}

export type ProviderCatalogItem = {
  kind: string
  provider_id: string
  models: string[]
  secret_configurable: boolean
}

export function listProviders(): Promise<ProviderCatalogItem[]> {
  return request<ProviderCatalogItem[]>('/api/v1/control/providers')
}

export type DeviceSummary = {
  id: string
  device_id: string
  alias: string
  agent_id: string | null
  online: boolean
  last_seen_at: string | null
  created_at?: string
  board?: string | null
  version?: string | null
}

export type ConversationSummary = {
  id: string
  agent_id: string | null
  device_id: string | null
  title: string
  summary: string
  locale: string
  turn_count: number
  started_at: string
  ended_at: string | null
}

export function listDevices(): Promise<DeviceSummary[]> {
  return request<DeviceSummary[]>('/api/v1/control/devices')
}

export function bindDevice(agentId: string, code: string): Promise<DeviceSummary> {
  return request<DeviceSummary>('/api/v1/control/devices/bind', {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId, code }),
  })
}

export function unbindDevice(deviceId: string): Promise<void> {
  return request<void>(`/api/v1/control/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  })
}

export function listConversations(agentId?: string): Promise<ConversationSummary[]> {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  return request<ConversationSummary[]>(`/api/v1/control/conversations${suffix}`)
}

export type ArtifactUploadResponse = {
  id: string
  size: number
  sha256: string
}

export type FirmwareReleaseSummary = {
  id: string
  artifact_id: string
  version: string
  board: string
  chip: string
  partition: string
  file_size: number
  sha256: string
  force: boolean
  published: boolean
  created_at: string | null
}

export async function uploadOtaArtifact(body: Blob | ArrayBuffer | Uint8Array): Promise<ArtifactUploadResponse> {
  return request<ArtifactUploadResponse>('/api/v1/control/ota/artifacts', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
    },
    body: body as BodyInit,
  })
}

export function createOtaRelease(payload: {
  artifact_id: string
  version: string
  board: string
  chip: string
  partition: string
  force?: boolean
}): Promise<FirmwareReleaseSummary> {
  return request<FirmwareReleaseSummary>('/api/v1/control/ota/releases', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function publishOtaRelease(releaseId: string): Promise<FirmwareReleaseSummary> {
  return request<FirmwareReleaseSummary>(`/api/v1/control/ota/releases/${encodeURIComponent(releaseId)}/publish`, {
    method: 'POST',
  })
}
