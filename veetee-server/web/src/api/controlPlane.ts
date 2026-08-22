import { reactive } from 'vue'
import type { AgentSummary } from '@/types/agent'

// Re-export để các view import kiểu AgentSummary thống nhất từ module API.
export type { AgentSummary }

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
  enabled?: boolean
  default?: boolean
  is_default?: boolean
  health?: { status: string; details?: string }
  config_version?: number
}

export function listProviders(): Promise<ProviderCatalogItem[]> {
  return request<ProviderCatalogItem[]>('/api/v1/control/providers')
}

export function updateProvider(
  kind: string,
  providerId: string,
  payload: { expected_version: number; enabled?: boolean; is_default?: boolean },
): Promise<ProviderCatalogItem> {
  return request<ProviderCatalogItem>(`/api/v1/control/providers/${encodeURIComponent(kind)}/${encodeURIComponent(providerId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function checkProviderHealth(kind: string, providerId: string): Promise<void> {
  return request<void>(`/api/v1/control/providers/${encodeURIComponent(kind)}/${encodeURIComponent(providerId)}/health-check`, {
    method: 'POST',
  })
}

// ------------------------------------------------------------------ Snapshots, Templates, Tags
export type AgentSnapshot = {
  id: string
  agent_id: string
  version: number
  reason: string
  created_by: string | null
  created_at: string
  config: Record<string, unknown>
}

export function listSnapshots(agentId: string): Promise<AgentSnapshot[]> {
  return request<AgentSnapshot[]>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/snapshots`)
}

export function createSnapshot(agentId: string, reason?: string): Promise<AgentSnapshot> {
  return request<AgentSnapshot>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/snapshots`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason ?? 'Thủ công từ giao diện' }),
  })
}

export function restoreSnapshot(agentId: string, snapshotId: string, expectedAgentVersion: number): Promise<AgentSummary> {
  return request<AgentSummary>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/snapshots/${encodeURIComponent(snapshotId)}/restore`, {
    method: 'POST',
    body: JSON.stringify({ expected_agent_version: expectedAgentVersion }),
  })
}

export type AgentTemplate = {
  id: string
  name: string
  description: string
  config: Record<string, unknown>
  created_at: string
}

export function listTemplates(): Promise<AgentTemplate[]> {
  return request<AgentTemplate[]>('/api/v1/control/templates')
}

export function createTemplate(name: string, description: string, config: Record<string, unknown>): Promise<AgentTemplate> {
  return request<AgentTemplate>('/api/v1/control/templates', {
    method: 'POST',
    body: JSON.stringify({ name, description, config }),
  })
}

export type AgentTag = {
  id: string
  name: string
  created_at: string
}

export function listTags(): Promise<AgentTag[]> {
  return request<AgentTag[]>('/api/v1/control/tags')
}

export function createTag(name: string): Promise<AgentTag> {
  return request<AgentTag>('/api/v1/control/tags', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}

export function assignAgentTag(tagId: string, agentId: string): Promise<void> {
  return request<void>(`/api/v1/control/tags/${encodeURIComponent(tagId)}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
  })
}

export function unassignAgentTag(tagId: string, agentId: string): Promise<void> {
  return request<void>(`/api/v1/control/tags/${encodeURIComponent(tagId)}/agents/${encodeURIComponent(agentId)}`, {
    method: 'DELETE',
  })
}

// ------------------------------------------------------------------ Knowledge / Datasets
export type KnowledgeDataset = {
  id: string
  name: string
  description: string
  status: 'active' | 'archived'
  version: number
  document_count?: number
  created_at?: string
  updated_at?: string
}

export type KnowledgeDocument = {
  id: string
  dataset_id: string
  filename: string
  media_type: string
  byte_size: number
  sha256: string
  status: string
  chunk_count: number
  created_at?: string
}

export type KnowledgeChunk = {
  id: string
  document_id: string
  ordinal: number
  content: string
  token_estimate: number
}

export function listDatasets(): Promise<KnowledgeDataset[]> {
  return request<KnowledgeDataset[]>('/api/v1/control/knowledge/datasets')
}

export function createDataset(payload: { name: string; description?: string }): Promise<KnowledgeDataset> {
  return request<KnowledgeDataset>('/api/v1/control/knowledge/datasets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getDataset(id: string): Promise<KnowledgeDataset> {
  return request<KnowledgeDataset>(`/api/v1/control/knowledge/datasets/${encodeURIComponent(id)}`)
}

export function updateDataset(id: string, payload: { name?: string; description?: string }): Promise<KnowledgeDataset> {
  return request<KnowledgeDataset>(`/api/v1/control/knowledge/datasets/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteDataset(id: string): Promise<void> {
  return request<void>(`/api/v1/control/knowledge/datasets/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export function listDocuments(datasetId: string): Promise<KnowledgeDocument[]> {
  return request<KnowledgeDocument[]>(`/api/v1/control/knowledge/datasets/${encodeURIComponent(datasetId)}/documents`)
}

export function uploadDocument(
  datasetId: string,
  filename: string,
  body: Blob | ArrayBuffer | Uint8Array,
  contentType: 'text/plain' | 'text/markdown' = 'text/plain',
): Promise<KnowledgeDocument> {
  return request<KnowledgeDocument>(
    `/api/v1/control/knowledge/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(filename)}`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': contentType,
      },
      body: body as BodyInit,
    },
  )
}

export function getDocumentChunks(documentId: string): Promise<KnowledgeChunk[]> {
  return request<KnowledgeChunk[]>(`/api/v1/control/knowledge/documents/${encodeURIComponent(documentId)}/chunks`)
}

export function deleteDocument(documentId: string): Promise<void> {
  return request<void>(`/api/v1/control/knowledge/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
  })
}

export function listAgentDatasets(agentId: string): Promise<KnowledgeDataset[]> {
  return request<KnowledgeDataset[]>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/knowledge/datasets`)
}

export function assignAgentDataset(agentId: string, datasetId: string): Promise<void> {
  return request<void>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/knowledge/datasets/${encodeURIComponent(datasetId)}`, {
    method: 'PUT',
  })
}

export function unassignAgentDataset(agentId: string, datasetId: string): Promise<void> {
  return request<void>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/knowledge/datasets/${encodeURIComponent(datasetId)}`, {
    method: 'DELETE',
  })
}

export type SearchResultItem = {
  chunk_id: string
  document_id: string
  score: number
  content: string
  filename?: string
}

export function searchKnowledge(payload: { dataset_ids: string[]; query: string; limit?: number; max_chars?: number }): Promise<{ results: SearchResultItem[]; count: number }> {
  return request<{ results: SearchResultItem[]; count: number }>('/api/v1/control/knowledge/search', {
    method: 'POST',
    body: JSON.stringify({ limit: 10, max_chars: 2000, ...payload }),
  })
}

// ------------------------------------------------------------------ Corrections & Context
export type CorrectionSet = {
  id: string
  name: string
  agent_id: string | null
  enabled: boolean
  version: number
  created_at?: string
}

export type CorrectionRule = {
  id: string
  set_id: string
  ordinal: number
  rule_type: 'exact' | 'phrase'
  pattern: string
  replacement: string
  case_sensitive: boolean
  enabled: boolean
}

export function listCorrectionSets(): Promise<CorrectionSet[]> {
  return request<CorrectionSet[]>('/api/v1/control/corrections/sets')
}

export function createCorrectionSet(payload: { name: string; agent_id?: string | null; enabled?: boolean }): Promise<CorrectionSet> {
  return request<CorrectionSet>('/api/v1/control/corrections/sets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateCorrectionSet(setId: string, payload: { name?: string; enabled?: boolean; expected_version: number }): Promise<CorrectionSet> {
  return request<CorrectionSet>(`/api/v1/control/corrections/sets/${encodeURIComponent(setId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteCorrectionSet(setId: string): Promise<void> {
  return request<void>(`/api/v1/control/corrections/sets/${encodeURIComponent(setId)}`, {
    method: 'DELETE',
  })
}

export function listCorrectionRules(setId: string): Promise<CorrectionRule[]> {
  return request<CorrectionRule[]>(`/api/v1/control/corrections/sets/${encodeURIComponent(setId)}/rules`)
}

export function createCorrectionRule(setId: string, payload: {
  ordinal: number
  rule_type: string
  pattern: string
  replacement: string
  case_sensitive?: boolean
  enabled?: boolean
  expected_set_version: number
}): Promise<CorrectionRule> {
  return request<CorrectionRule>(`/api/v1/control/corrections/sets/${encodeURIComponent(setId)}/rules`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteCorrectionRule(ruleId: string): Promise<void> {
  return request<void>(`/api/v1/control/corrections/rules/${encodeURIComponent(ruleId)}`, {
    method: 'DELETE',
  })
}

export type CorrectionPreviewResponse = {
  set_id: string
  version: number
  original_text: string
  corrected_text: string
  applied_rules: Array<{ rule_id: string; pattern: string; replacement: string }>
}

export function previewCorrectionSet(setId: string, text: string): Promise<CorrectionPreviewResponse> {
  return request<CorrectionPreviewResponse>(`/api/v1/control/corrections/sets/${encodeURIComponent(setId)}/preview`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
}

export type ContextProviderConfig = {
  id: string
  agent_id: string
  provider_type: string
  enabled: boolean
  ordinal: number
  timeout_ms: number
  cache_ttl_seconds: number
  config: Record<string, unknown>
  version: number
}

export function listAgentContextProviders(agentId: string): Promise<ContextProviderConfig[]> {
  return request<ContextProviderConfig[]>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/context-providers`)
}

export function putAgentContextProvider(
  agentId: string,
  providerType: string,
  payload: { enabled: boolean; ordinal: number; timeout_ms: number; cache_ttl_seconds: number; config: Record<string, unknown> },
): Promise<ContextProviderConfig> {
  return request<ContextProviderConfig>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/context-providers/${encodeURIComponent(providerType)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteAgentContextProvider(agentId: string, providerType: string): Promise<void> {
  return request<void>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/context-providers/${encodeURIComponent(providerType)}`, {
    method: 'DELETE',
  })
}

// ------------------------------------------------------------------ External Endpoints & Device MCP
export type ExternalEndpoint = {
  id: string
  name: string
  url: string
  auth_header_env: string
  enabled: boolean
  version: number
}

export type IntegrationPermission = {
  agent_id: string
  endpoint_id: string
  can_list: boolean
  can_call: boolean
  rate_limit_calls: number
  rate_limit_window_seconds: number
}

export function listEndpoints(): Promise<ExternalEndpoint[]> {
  return request<ExternalEndpoint[]>('/api/v1/control/integrations/endpoints')
}

export function createEndpoint(payload: { name: string; url: string; auth_header_env?: string; enabled?: boolean }): Promise<ExternalEndpoint> {
  return request<ExternalEndpoint>('/api/v1/control/integrations/endpoints', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getEndpoint(id: string): Promise<ExternalEndpoint> {
  return request<ExternalEndpoint>(`/api/v1/control/integrations/endpoints/${encodeURIComponent(id)}`)
}

export function updateEndpoint(id: string, payload: { name?: string; url?: string; auth_header_env?: string; enabled?: boolean; expected_version: number }): Promise<ExternalEndpoint> {
  return request<ExternalEndpoint>(`/api/v1/control/integrations/endpoints/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteEndpoint(id: string): Promise<void> {
  return request<void>(`/api/v1/control/integrations/endpoints/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export function listAgentPermissions(agentId: string): Promise<IntegrationPermission[]> {
  return request<IntegrationPermission[]>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/integration-permissions`)
}

export function putAgentPermission(agentId: string, endpointId: string, payload: { can_list: boolean; can_call: boolean; rate_limit_calls?: number; rate_limit_window_seconds?: number }): Promise<IntegrationPermission> {
  return request<IntegrationPermission>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/integration-permissions/${encodeURIComponent(endpointId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function deleteAgentPermission(agentId: string, endpointId: string): Promise<void> {
  return request<void>(`/api/v1/control/agents/${encodeURIComponent(agentId)}/integration-permissions/${encodeURIComponent(endpointId)}`, {
    method: 'DELETE',
  })
}

export function testToolsList(endpointId: string, agentId: string): Promise<{ tools: Array<{ name: string; description: string }> }> {
  return request<{ tools: Array<{ name: string; description: string }> }>(`/api/v1/control/integrations/endpoints/${encodeURIComponent(endpointId)}/test/list`, {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId }),
  })
}

export function testToolCall(endpointId: string, payload: { agent_id: string; tool_name: string; arguments: Record<string, unknown> }): Promise<{ content: unknown[]; isError: boolean }> {
  return request<{ content: unknown[]; isError: boolean }>(`/api/v1/control/integrations/endpoints/${encodeURIComponent(endpointId)}/test/call`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export type DeviceMcpTool = {
  name: string
  description?: string
  inputSchema?: Record<string, unknown>
}

export function listDeviceMcpTools(devicePk: string, sessionId?: string): Promise<{ session_id: string; tools: DeviceMcpTool[] }> {
  return request<{ session_id: string; tools: DeviceMcpTool[] }>(`/api/v1/control/devices/${encodeURIComponent(devicePk)}/mcp/tools/list`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId }),
  })
}

export type DeviceMcpPrepareResponse = {
  confirmation_token: string
  expires_in_seconds: number
  session_id: string
  tool_name: string
  binding_sha256: string
}

export function prepareDeviceMcpCall(devicePk: string, toolName: string, payload: { session_id?: string; arguments: Record<string, unknown> }): Promise<DeviceMcpPrepareResponse> {
  return request<DeviceMcpPrepareResponse>(`/api/v1/control/devices/${encodeURIComponent(devicePk)}/mcp/tools/${encodeURIComponent(toolName)}/prepare-call`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function confirmDeviceMcpCall(devicePk: string, toolName: string, confirmationToken: string): Promise<{ content: unknown[]; is_error: boolean; truncated: boolean }> {
  return request<{ content: unknown[]; is_error: boolean; truncated: boolean }>(`/api/v1/control/devices/${encodeURIComponent(devicePk)}/mcp/tools/${encodeURIComponent(toolName)}/call`, {
    method: 'POST',
    body: JSON.stringify({ confirmation_token: confirmationToken }),
  })
}

// ------------------------------------------------------------------ Administration (Users, Settings, Quotas, Audit)
export type AdminUser = {
  id: string
  email: string
  role: 'owner' | 'admin'
  status: 'active' | 'suspended'
  version: number
  created_at?: string
}

export function listUsers(params?: { page?: number; limit?: number; role?: string; status?: string; search?: string }): Promise<{ items: AdminUser[]; total: number; page: number; limit: number }> {
  const query = new URLSearchParams()
  if (params?.page) query.set('page', String(params.page))
  if (params?.limit) query.set('limit', String(params.limit))
  if (params?.role) query.set('role', params.role)
  if (params?.status) query.set('status', params.status)
  if (params?.search) query.set('search', params.search)
  const qs = query.toString() ? `?${query.toString()}` : ''
  return request<{ items: AdminUser[]; total: number; page: number; limit: number }>(`/api/v1/control/admin/users${qs}`)
}

export function createUser(payload: { email: string; role: string; status: string }): Promise<{ user: AdminUser; reset_token: string }> {
  return request<{ user: AdminUser; reset_token: string }>('/api/v1/control/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateUser(userId: string, payload: { expected_version: number; role?: string; status?: string }): Promise<AdminUser> {
  return request<AdminUser>(`/api/v1/control/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function issueUserResetToken(userId: string): Promise<{ reset_token: string; expires_in_seconds: number }> {
  return request<{ reset_token: string; expires_in_seconds: number }>(`/api/v1/control/admin/users/${encodeURIComponent(userId)}/reset-token`, {
    method: 'POST',
  })
}

// Shape khớp SystemSettingsRepository (backend 3d6b33d): không có schema_type/description.
export type SettingItem = {
  key: string
  value: unknown
  version: number
  updated_by?: string | null
  created_at?: string
  updated_at?: string
}

export function listSettings(): Promise<SettingItem[]> {
  return request<SettingItem[]>('/api/v1/control/admin/settings')
}

export function getSetting(key: string): Promise<SettingItem> {
  return request<SettingItem>(`/api/v1/control/admin/settings/${encodeURIComponent(key)}`)
}

export function updateSetting(key: string, payload: { value: unknown; expected_version: number }): Promise<SettingItem> {
  return request<SettingItem>(`/api/v1/control/admin/settings/${encodeURIComponent(key)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

// Shape khớp QuotaService.get_effective_quota_and_usage (backend 3d6b33d):
// policy có thể null khi user chưa có policy riêng; usage nằm trong metrics theo cửa sổ.
export type QuotaPolicy = {
  user_id: string
  llm_tokens_per_day: number | null
  tts_chars_per_day: number | null
  tool_calls_per_minute: number | null
  rag_bytes_per_month: number | null
  enabled: boolean
  version: number
  updated_by?: string | null
  created_at?: string
  updated_at?: string
}

export type QuotaMetricUsage = {
  limit: number | null
  used: number
  remaining: number | null
  window_start: string
}

export type QuotaMetricKey = 'llm_tokens_day' | 'tts_chars_day' | 'tool_calls_minute' | 'rag_bytes_month'

export type QuotaEffective = {
  user_id: string
  enabled: boolean
  policy: QuotaPolicy | null
  metrics: Record<QuotaMetricKey, QuotaMetricUsage>
}

export function getUserQuota(userId: string): Promise<QuotaEffective> {
  return request<QuotaEffective>(`/api/v1/control/admin/quotas/${encodeURIComponent(userId)}`)
}

export function updateUserQuota(userId: string, payload: {
  expected_version: number
  enabled?: boolean
  llm_tokens_per_day?: number
  tts_chars_per_day?: number
  tool_calls_per_minute?: number
  rag_bytes_per_month?: number
}): Promise<QuotaPolicy> {
  return request<QuotaPolicy>(`/api/v1/control/admin/quotas/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function getMyQuota(): Promise<QuotaEffective> {
  return request<QuotaEffective>('/api/v1/control/quotas/me')
}

export type AuditLogItem = {
  id: string
  actor_user_id: string | null
  action: string
  resource_type: string
  resource_id: string
  metadata: Record<string, unknown>
  created_at: string
}

export function searchAuditLogs(params?: {
  page?: number
  limit?: number
  action?: string
  resource_type?: string
  actor_user_id?: string
  start_time?: string
  end_time?: string
}): Promise<{ items: AuditLogItem[]; total: number; page: number; limit: number }> {
  const query = new URLSearchParams()
  if (params?.page) query.set('page', String(params.page))
  if (params?.limit) query.set('limit', String(params.limit))
  if (params?.action) query.set('action', params.action)
  if (params?.resource_type) query.set('resource_type', params.resource_type)
  if (params?.actor_user_id) query.set('actor_user_id', params.actor_user_id)
  if (params?.start_time) query.set('start_time', params.start_time)
  if (params?.end_time) query.set('end_time', params.end_time)
  const qs = query.toString() ? `?${query.toString()}` : ''
  return request<{ items: AuditLogItem[]; total: number; page: number; limit: number }>(`/api/v1/control/admin/audit-logs${qs}`)
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
