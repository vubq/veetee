import type { AgentSummary } from '@/types/agent'

type AgentWire = {
  id: string; name: string; version: number; role_prompt: string; personality: string
  address_style: string; language: string; detail_level: string; response_style: string
  model_id: string; voice_id: string; intent_strategy: string; memory_enabled: boolean
  memory_min_confidence: number; tool_policy: Record<string, unknown>; memory_policy: Record<string, unknown>
  device_count?: number; online?: boolean; last_conversation?: string | null
}

function toAgentSummary(agent: AgentWire): AgentSummary {
  return {
    id: agent.id, name: agent.name, role: agent.personality || agent.role_prompt,
    model: agent.model_id, lastConversation: agent.last_conversation || 'Chưa có dữ liệu',
    deviceCount: agent.device_count ?? 0, online: agent.online ?? false, version: agent.version,
    rolePrompt: agent.role_prompt, personality: agent.personality, addressStyle: agent.address_style,
    language: agent.language, detailLevel: agent.detail_level, responseStyle: agent.response_style,
    modelId: agent.model_id, voiceId: agent.voice_id, intentStrategy: agent.intent_strategy,
    memoryEnabled: agent.memory_enabled, memoryMinConfidence: agent.memory_min_confidence,
    toolPolicy: agent.tool_policy, memoryPolicy: agent.memory_policy,
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8080'
let accessToken = ''

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  json?: unknown
  body?: BodyInit | null
  idempotencyKey?: string
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { json, idempotencyKey, headers: customHeaders, ...init } = options
  const headers = new Headers(customHeaders)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey)
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers,
    body: json === undefined ? options.body : JSON.stringify(json),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as {
      detail?: string | Array<{ msg?: string }>; code?: string; message?: string; request_id?: string
    }
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join(', ')
      : body.detail
    throw new ApiError(
      response.status,
      body.message || detail || `API error ${response.status}`,
      body.code,
      body.request_id || response.headers.get('X-Veetee-Request-Id') || undefined,
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function login(email: string, password: string): Promise<void> {
  const response = await request<{ access_token: string }>('/api/v1/control/auth/login', {
    method: 'POST', json: { email, password },
  })
  accessToken = response.access_token
}

export function logout() { accessToken = '' }
export function isAuthenticated() { return Boolean(accessToken) }

export function listAgents(): Promise<AgentSummary[]> {
  return request<AgentWire[]>('/api/v1/control/agents').then((agents) => agents.map(toAgentSummary))
}

export function updateAgent(agent: AgentSummary): Promise<AgentSummary> {
  return request<AgentWire>(`/api/v1/control/agents/${agent.id}`, {
    method: 'PUT',
    json: {
      name: agent.name, role_prompt: agent.rolePrompt ?? '', personality: agent.personality ?? '',
      address_style: agent.addressStyle ?? '', language: agent.language ?? 'vi-VN',
      detail_level: agent.detailLevel ?? 'adaptive', response_style: agent.responseStyle ?? '',
      model_id: agent.modelId ?? '', voice_id: agent.voiceId ?? '',
      intent_strategy: agent.intentStrategy ?? 'function_call', memory_enabled: agent.memoryEnabled ?? true,
      memory_min_confidence: agent.memoryMinConfidence ?? 0.8, tool_policy: agent.toolPolicy ?? {},
      memory_policy: agent.memoryPolicy ?? {}, expected_version: agent.version,
    },
  }).then(toAgentSummary)
}

export function createAgent(name: string, rolePrompt: string): Promise<AgentSummary> {
  return request<AgentWire>('/api/v1/control/agents', {
    method: 'POST', json: { name, role_prompt: rolePrompt },
  }).then(toAgentSummary)
}

export type DeviceSummary = {
  id: string; device_id: string; client_id: string; alias: string; status: string
  board: string; chip: string; partition: string; current_firmware_version: string
  auto_update: boolean; channel: string; cohort: string; online: boolean
  owner_user_id: string | null; agent_id: string | null
}

export type DeviceBindInput = { device_id: string; code: string; alias: string; agent_id: string | null }
export const listDevices = () => request<DeviceSummary[]>('/api/v1/control/devices')
export const bindDevice = (input: DeviceBindInput, idempotencyKey: string) =>
  request<DeviceSummary>('/api/v1/control/devices/bind', { method: 'POST', json: input, idempotencyKey })
export const patchDevice = (deviceId: string, input: Partial<Pick<DeviceSummary, 'alias' | 'agent_id' | 'auto_update' | 'channel'>>) =>
  request<DeviceSummary>(`/api/v1/control/devices/${encodeURIComponent(deviceId)}`, { method: 'PATCH', json: input })
export const unbindDevice = (deviceId: string, idempotencyKey: string) =>
  request<DeviceSummary>(`/api/v1/control/devices/${encodeURIComponent(deviceId)}/unbind`, { method: 'POST', idempotencyKey })
export const recoverDevice = (deviceId: string, clientId: string) =>
  request<{ device: DeviceSummary; recovery_token: string }>(`/api/v1/control/devices/${encodeURIComponent(deviceId)}/recover`, {
    method: 'POST', json: { client_id: clientId },
  })

export type OtaArtifact = {
  id: string; board: string; chip: string; partition: string; file_name: string; file_size: number
  sha256: string; signature: string; signature_algorithm: string; signature_key_id: string
  provenance: string; metadata: Record<string, unknown>; created_at: string
}
export type OtaRelease = {
  id: string; version: string; artifact_id: string; board: string; chip: string; partition: string
  channel: string; min_current_version: string; is_published: boolean; published_at: string | null
  created_at: string; provenance: string; rollback_target_id: string | null
}
export type OtaRollout = {
  id: string; release_id: string; channel: string; cohort_percentage: number
  status: 'active' | 'paused' | 'killed' | string; created_at: string; updated_at: string
  kind: 'release' | 'rollback' | string; rollback_scope: string | null
  rollback_device_id: string | null; rollback_cohort: string | null
}
export type OtaSummary = {
  total_devices: number; bound_devices: number; total_releases: number; active_rollouts: number
  total_reports: number; devices_by_board_version_cohort: Array<{ board: string; version: string; cohort: string; count: number }>
}
export type ArtifactUpload = {
  file: File; sha256: string; signature: string; board: string; chip: string; partition: string; provenance: string
}
export type ReleaseCreate = {
  version: string; artifact_id: string; board: string; chip: string; partition: string; channel: string
  min_current_version: string; provenance: string; rollback_target_id: string | null; is_published: false
}

const otaPath = '/api/v1/control/ota'
export const listArtifacts = () => request<OtaArtifact[]>(`${otaPath}/artifacts`)
export const uploadArtifact = (input: ArtifactUpload) => request<OtaArtifact>(`${otaPath}/artifacts`, {
  method: 'POST', body: input.file,
  headers: {
    'Content-Type': 'application/octet-stream', 'X-Artifact-SHA256': input.sha256,
    'X-Artifact-Signature': input.signature, 'X-Artifact-Name': input.file.name,
    'X-Artifact-Board': input.board, 'X-Artifact-Chip': input.chip,
    'X-Artifact-Partition': input.partition, 'X-Artifact-Provenance': input.provenance,
  },
})
export const listReleases = () => request<OtaRelease[]>(`${otaPath}/releases`)
export const createRelease = (input: ReleaseCreate) => request<OtaRelease>(`${otaPath}/releases`, { method: 'POST', json: input })
export const publishRelease = (id: string, percentage: number) => request<OtaRelease>(`${otaPath}/releases/${id}/publish?percentage=${percentage}`, { method: 'POST' })
export const listRollouts = () => request<OtaRollout[]>(`${otaPath}/rollouts`)
export const changeRollout = (id: string, action: 'pause' | 'resume' | 'kill') => request<OtaRollout>(`${otaPath}/rollouts/${id}/${action}`, { method: 'POST' })
export const rollbackRollout = (id: string, body: { scope: 'rollout' | 'cohort' | 'device'; device_id?: string; cohort?: string }) =>
  request<OtaRollout>(`${otaPath}/rollouts/${id}/rollback`, { method: 'POST', json: body })
export const getOtaSummary = () => request<OtaSummary>(`${otaPath}/summary`)

export type ConversationSummary = {
  id: string; agent_id: string | null; device_id: string | null; title: string; summary: string
  locale: string; turn_count: number; started_at: string; ended_at: string | null
}
export function listConversations(agentId?: string): Promise<ConversationSummary[]> {
  const suffix = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  return request<ConversationSummary[]>(`/api/v1/control/conversations${suffix}`)
}
