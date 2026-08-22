import type { AgentSummary } from '@/types/agent'

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

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new ApiError(response.status, body.detail ?? `API error ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function login(email: string, password: string): Promise<void> {
  const response = await request<{ access_token: string }>('/api/v1/control/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  accessToken = response.access_token
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

export type DeviceSummary = {
  id: string
  device_id: string
  alias: string
  agent_id: string | null
  online: boolean
  last_seen_at: string | null
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

export function listConversations(agentId?: string): Promise<ConversationSummary[]> {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  return request<ConversationSummary[]>(`/api/v1/control/conversations${suffix}`)
}
