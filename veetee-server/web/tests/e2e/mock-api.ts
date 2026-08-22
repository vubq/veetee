import type { Page, Request, Route } from '@playwright/test'

export type AgentWire = {
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

export type MockApiState = {
  loginStatus: number
  authorized: boolean
  // Token của phiên hiện tại; mỗi lần login thành công phát hành token mới
  // để mô phỏng đúng server: token cũ sau khi đăng xuất/expiry sẽ bị từ chối.
  currentToken: string
  tokenCounter: number
  expireNextRequest: boolean
  agents: AgentWire[]
  devices: Array<Record<string, unknown>>
  memories: Array<Record<string, unknown>>
  conversations: Array<Record<string, unknown>>
  requests: Array<{ method: string; path: string; contentType: string; body?: Record<string, unknown> }>
  bindStatus: number
  uploadStatus: number
  conversationStatus: number
  logoutStatus: number
  logoutNetworkError: boolean
  // Trạng thái trả về cho GET /providers (catalog nhà cung cấp) và PUT /agents/{id}.
  providersStatus: number
  updateStatus: number
  forbiddenPaths: string[]
  // Các request khớp pattern sẽ bị giữ lại (deferred) thay vì trả lời ngay;
  // test giải phóng thủ công qua heldRequests để mô phỏng response đến muộn.
  holdPatterns: Array<{ method: string; path: string }>
  heldRequests: Array<HeldRequest>
}

export type HeldRequest = {
  method: string
  path: string
  respond: (status: number, body?: unknown) => Promise<void>
}

export function agent(overrides: Partial<AgentWire> = {}): AgentWire {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    name: 'Trợ lý gia đình',
    version: 1,
    role_prompt: 'Hỗ trợ gia đình bằng tiếng Việt.',
    personality: '',
    address_style: '',
    language: 'vi-VN',
    detail_level: 'adaptive',
    response_style: 'balanced',
    model_id: 'groq/openai/gpt-oss-120b',
    voice_id: '',
    intent_strategy: 'function_call',
    memory_enabled: true,
    memory_min_confidence: 0.8,
    tool_policy: {},
    memory_policy: {},
    ...overrides,
  }
}

export function createState(overrides: Partial<MockApiState> = {}): MockApiState {
  return {
    loginStatus: 200,
    authorized: false,
    currentToken: '',
    tokenCounter: 0,
    expireNextRequest: false,
    agents: [agent()],
    devices: [],
    memories: [{
      id: '22222222-2222-4222-8222-222222222222',
      agent_id: '11111111-1111-4111-8111-111111111111',
      kind: 'profile',
      content: 'Người dùng ưu tiên tiếng Việt.',
      provenance: 'user_explicit',
      confidence: 1,
      metadata: {},
    }],
    conversations: [],
    requests: [],
    bindStatus: 200,
    uploadStatus: 201,
    conversationStatus: 200,
    logoutStatus: 204,
    logoutNetworkError: false,
    providersStatus: 200,
    updateStatus: 200,
    forbiddenPaths: [],
    holdPatterns: [],
    heldRequests: [],
    ...overrides,
  }
}

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

function bodyJson(request: Request): Record<string, unknown> {
  return request.postDataJSON() as Record<string, unknown>
}

// Ghi lại body JSON của request để test xác minh payload; GET/DELETE không có body.
function optionalBody(request: Request): Record<string, unknown> | undefined {
  const text = request.postData()
  if (!text) return undefined
  try {
    return JSON.parse(text) as Record<string, unknown>
  } catch {
    return undefined
  }
}

export async function installMockApi(page: Page, state: MockApiState) {
  await page.route('http://127.0.0.1:8080/api/v1/control/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname + url.search
    const method = request.method()
    state.requests.push({ method, path, contentType: await request.headerValue('content-type') ?? '', body: optionalBody(request) })

    if (path === '/api/v1/control/auth/login' && method === 'POST') {
      if (state.loginStatus !== 200) return json(route, state.loginStatus, { detail: 'Thông tin đăng nhập không đúng' })
      state.tokenCounter += 1
      state.currentToken = `test-token-${state.tokenCounter}`
      state.authorized = true
      return json(route, 200, { access_token: state.currentToken, token_type: 'bearer' })
    }

    if (path === '/api/v1/control/auth/logout' && method === 'POST' && state.logoutNetworkError) {
      // Mô phỏng mất kết nối trước khi request tới được server revoke.
      return route.abort('connectionreset')
    }

    const expectedAuthorization = `Bearer ${state.currentToken}`
    const authorization = request.headers()['authorization'] ?? ''
    if (!state.authorized || authorization !== expectedAuthorization || state.expireNextRequest) {
      state.expireNextRequest = false
      state.authorized = false
      return json(route, 401, { detail: 'Invalid or expired session' })
    }

    if (state.forbiddenPaths.includes(url.pathname)) {
      return json(route, 403, { detail: 'Admin role required' })
    }

    // Request khớp pattern bị giữ lại để test quyết định thời điểm và trạng thái phản hồi;
    // so khớp theo pathname để bắt cả biến thể có query (?agent_id=...).
    const holdIndex = state.holdPatterns.findIndex((pattern) => pattern.method === method && url.pathname === pattern.path)
    if (holdIndex >= 0) {
      state.holdPatterns.splice(holdIndex, 1)
      const held: HeldRequest = {
        method,
        path,
        respond: (status, body) => json(route, status, body ?? {}),
      }
      state.heldRequests.push(held)
      return
    }

    if (path === '/api/v1/control/auth/logout' && method === 'POST') {
      state.authorized = false
      if (state.logoutStatus !== 204) return json(route, state.logoutStatus, { detail: 'Không thu hồi được phiên trên máy chủ' })
      return route.fulfill({ status: 204 })
    }
    if (path === '/api/v1/control/agents' && method === 'GET') return json(route, 200, state.agents)
    if (path === '/api/v1/control/agents' && method === 'POST') {
      const payload = bodyJson(request)
      const created = agent({
        id: '33333333-3333-4333-8333-333333333333',
        name: String(payload.name),
        role_prompt: String(payload.role_prompt ?? ''),
      })
      state.agents.push(created)
      return json(route, 201, created)
    }
    const agentMatch = url.pathname.match(/\/api\/v1\/control\/agents\/([^/]+)$/)
    if (agentMatch && method === 'PUT') {
      if (state.updateStatus !== 200) {
        const detail = state.updateStatus === 409 ? 'Agent changed or does not exist' : 'Update failed'
        return json(route, state.updateStatus, { detail })
      }
      const index = state.agents.findIndex((item) => item.id === agentMatch[1])
      const { expected_version: _expectedVersion, ...payload } = bodyJson(request)
      const updated = { ...state.agents[index], ...payload, version: (state.agents[index]?.version ?? 0) + 1 }
      state.agents[index] = updated as AgentWire
      return json(route, 200, updated)
    }
    if (agentMatch && method === 'DELETE') {
      state.agents = state.agents.filter((item) => item.id !== agentMatch[1])
      return route.fulfill({ status: 204 })
    }
    if (url.pathname === '/api/v1/control/devices' && method === 'GET') return json(route, 200, state.devices)
    if (url.pathname === '/api/v1/control/devices/bind' && method === 'POST') {
      if (state.bindStatus !== 200) return json(route, state.bindStatus, { detail: 'Mã kích hoạt không hợp lệ' })
      const payload = bodyJson(request)
      const device = {
        id: '44444444-4444-4444-8444-444444444444',
        device_id: 'device-test',
        alias: '',
        agent_id: payload.agent_id,
        online: false,
        last_seen_at: null,
      }
      state.devices.push(device)
      return json(route, 200, device)
    }
    const deviceMatch = url.pathname.match(/\/api\/v1\/control\/devices\/([^/]+)$/)
    if (deviceMatch && method === 'DELETE') {
      state.devices = state.devices.filter((item) => item.id !== deviceMatch[1])
      return route.fulfill({ status: 204 })
    }
    if (url.pathname === '/api/v1/control/providers' && method === 'GET') {
      if (state.providersStatus !== 200) return json(route, state.providersStatus, { detail: 'Không tải được danh sách nhà cung cấp' })
      // Phản chiếu đúng shape và nội dung catalog backend (kind asr/llm/tts) để UI
      // phải lọc kind=llm mới lấy được danh sách mô hình.
      return json(route, 200, [
        { kind: 'asr', provider_id: 'pho_whisper', models: ['mad1999/pho-whisper-small-ct2'], secret_configurable: false, enabled: true, is_default: true, config_version: 1, health: { status: 'healthy' } },
        { kind: 'llm', provider_id: 'omniroute', models: ['groq/openai/gpt-oss-120b', 'groq/qwen/qwen3.6-27b'], secret_configurable: false, enabled: true, is_default: true, config_version: 1, health: { status: 'healthy' } },
        { kind: 'tts', provider_id: 'vieneu', models: ['local'], secret_configurable: false, enabled: true, is_default: true, config_version: 1, health: { status: 'healthy' } },
      ])
    }
    const providerMatch = url.pathname.match(/\/api\/v1\/control\/providers\/([^/]+)\/([^/]+)$/)
    if (providerMatch && method === 'PATCH') {
      const payload = bodyJson(request)
      return json(route, 200, { kind: providerMatch[1], provider_id: providerMatch[2], models: [], secret_configurable: false, enabled: payload.enabled ?? true, is_default: payload.is_default ?? false, config_version: 2, health: { status: 'healthy' } })
    }
    if (/\/api\/v1\/control\/providers\/[^/]+\/[^/]+\/health-check$/.test(url.pathname) && method === 'POST') return route.fulfill({ status: 204 })

    const dataset = { id: '77777777-7777-4777-8777-777777777777', name: 'Tài liệu Veetee', description: 'Tài liệu vận hành', status: 'active', version: 1, created_at: '2026-08-22T00:00:00Z', updated_at: '2026-08-22T00:00:00Z' }
    if (url.pathname === '/api/v1/control/knowledge/datasets' && method === 'GET') return json(route, 200, [dataset])
    if (url.pathname === `/api/v1/control/knowledge/datasets/${dataset.id}/documents` && method === 'GET') return json(route, 200, [])
    const documentUpload = url.pathname.match(/\/api\/v1\/control\/knowledge\/datasets\/([^/]+)\/documents\/([^/]+)$/)
    if (documentUpload && method === 'PUT') return json(route, 201, { id: '88888888-8888-4888-8888-888888888888', dataset_id: documentUpload[1], filename: decodeURIComponent(documentUpload[2]), media_type: request.headers()['content-type'], byte_size: request.postDataBuffer()?.byteLength ?? 0, sha256: 'b'.repeat(64), status: 'ready', chunk_count: 1 })
    if (url.pathname === '/api/v1/control/knowledge/search' && method === 'POST') return json(route, 200, { count: 1, results: [{ chunk_id: '99999999-9999-4999-8999-999999999999', document_id: '88888888-8888-4888-8888-888888888888', score: 0.92, content: 'Veetee chạy trực tiếp trên máy local.', filename: 'van-hanh.md' }] })
    if (/\/api\/v1\/control\/agents\/[^/]+\/knowledge\/datasets$/.test(url.pathname) && method === 'GET') return json(route, 200, [])
    if (/\/api\/v1\/control\/agents\/[^/]+\/knowledge\/datasets\/[^/]+$/.test(url.pathname) && method === 'PUT') return route.fulfill({ status: 204 })

    const correctionSet = { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', name: 'Tiếng Việt', agent_id: null, enabled: true, version: 1 }
    if (url.pathname === '/api/v1/control/corrections/sets' && method === 'GET') return json(route, 200, [correctionSet])
    if (url.pathname === `/api/v1/control/corrections/sets/${correctionSet.id}/rules` && method === 'GET') return json(route, 200, [{ id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', set_id: correctionSet.id, ordinal: 1, rule_type: 'phrase', pattern: 'Xin chao', replacement: 'Xin chào', case_sensitive: false, enabled: true }])
    if (url.pathname === `/api/v1/control/corrections/sets/${correctionSet.id}/preview` && method === 'POST') {
      const original = String(bodyJson(request).text ?? '')
      return json(route, 200, { set_id: correctionSet.id, version: 1, original_text: original, corrected_text: original.replace(/Xin chao/gi, 'Xin chào'), applied_rules: [{ rule_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', pattern: 'Xin chao', replacement: 'Xin chào' }] })
    }
    if (/\/api\/v1\/control\/agents\/[^/]+\/context-providers$/.test(url.pathname) && method === 'GET') return json(route, 200, [])

    if (url.pathname === '/api/v1/control/integrations/endpoints' && method === 'GET') return json(route, 200, [{ id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', name: 'Thời tiết', url: 'https://weather.example.test/mcp', auth_header_env: 'WEATHER_TOKEN', enabled: true, version: 1 }])
    if (/\/api\/v1\/control\/agents\/[^/]+\/integration-permissions$/.test(url.pathname) && method === 'GET') return json(route, 200, [])
    const listDeviceTools = url.pathname.match(/\/api\/v1\/control\/devices\/([^/]+)\/mcp\/tools\/list$/)
    if (listDeviceTools && method === 'POST') return json(route, 200, { session_id: 'live-session-1', tools: [{ name: 'screen.set_brightness', description: 'Đặt độ sáng màn hình', inputSchema: { type: 'object' } }] })
    const prepareDeviceTool = url.pathname.match(/\/api\/v1\/control\/devices\/([^/]+)\/mcp\/tools\/([^/]+)\/prepare-call$/)
    if (prepareDeviceTool && method === 'POST') return json(route, 200, { confirmation_token: 'secret-confirmation-token-never-render', expires_in_seconds: 60, session_id: 'live-session-1', tool_name: decodeURIComponent(prepareDeviceTool[2]), binding_sha256: 'c'.repeat(64) })
    const callDeviceTool = url.pathname.match(/\/api\/v1\/control\/devices\/([^/]+)\/mcp\/tools\/([^/]+)\/call$/)
    if (callDeviceTool && method === 'POST') return json(route, 200, { content: [{ type: 'text', text: 'Đã cập nhật độ sáng' }], is_error: false, truncated: false })

    const adminUser = { id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', email: 'admin@example.test', role: 'admin', status: 'active', version: 1, created_at: '2026-08-22T00:00:00Z' }
    if (url.pathname === '/api/v1/control/admin/users' && method === 'GET') return json(route, 200, { items: [adminUser], total: 1, page: 1, limit: 50 })
    if (url.pathname === '/api/v1/control/admin/settings' && method === 'GET') return json(route, 200, [{ key: 'quota.default_enabled', value: false, version: 1 }])
    const quotaMetrics = { llm_tokens_day: { limit: 10000, used: 1200, remaining: 8800, window_start: '2026-08-22T00:00:00Z' }, tts_chars_day: { limit: 20000, used: 500, remaining: 19500, window_start: '2026-08-22T00:00:00Z' }, tool_calls_minute: { limit: 60, used: 2, remaining: 58, window_start: '2026-08-22T00:00:00Z' }, rag_bytes_month: { limit: 1048576, used: 1024, remaining: 1047552, window_start: '2026-08-01T00:00:00Z' } }
    if (url.pathname === '/api/v1/control/quotas/me' && method === 'GET') return json(route, 200, { user_id: adminUser.id, enabled: true, policy: null, metrics: quotaMetrics })
    if (url.pathname === `/api/v1/control/admin/quotas/${adminUser.id}` && method === 'GET') return json(route, 200, { user_id: adminUser.id, enabled: true, policy: { user_id: adminUser.id, llm_tokens_per_day: 10000, tts_chars_per_day: 20000, tool_calls_per_minute: 60, rag_bytes_per_month: 1048576, enabled: true, version: 1 }, metrics: quotaMetrics })
    if (url.pathname === '/api/v1/control/admin/audit-logs' && method === 'GET') return json(route, 200, { items: [{ id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', actor_user_id: adminUser.id, action: 'user.updated', resource_type: 'user', resource_id: adminUser.id, metadata: { version: 2 }, created_at: '2026-08-22T10:00:00Z' }], total: 1, page: 1, limit: 50 })
    if (url.pathname === '/api/v1/control/memories' && method === 'GET') return json(route, 200, state.memories)
    const memoryMatch = url.pathname.match(/\/api\/v1\/control\/memories\/([^/]+)$/)
    if (memoryMatch && method === 'DELETE') {
      state.memories = state.memories.filter((item) => item.id !== memoryMatch[1])
      return route.fulfill({ status: 204 })
    }
    if (url.pathname === '/api/v1/control/conversations' && method === 'GET') {
      if (state.conversationStatus !== 200) return json(route, state.conversationStatus, { detail: 'Không tải được lịch sử hội thoại' })
      return json(route, 200, state.conversations)
    }
    if (url.pathname === '/api/v1/control/ota/artifacts' && method === 'POST') {
      if (state.uploadStatus !== 201) return json(route, state.uploadStatus, { detail: 'Artifact không hợp lệ' })
      return json(route, 201, { id: '55555555-5555-4555-8555-555555555555', size: request.postDataBuffer()?.byteLength ?? 0, sha256: 'a'.repeat(64) })
    }
    if (url.pathname === '/api/v1/control/ota/releases' && method === 'POST') {
      const payload = bodyJson(request)
      return json(route, 201, {
        id: '66666666-6666-4666-8666-666666666666',
        ...payload,
        file_size: 4,
        sha256: 'a'.repeat(64),
        published: false,
        created_at: '2026-08-22T00:00:00Z',
      })
    }
    if (/\/api\/v1\/control\/ota\/releases\/[^/]+\/publish$/.test(url.pathname) && method === 'POST') {
      return json(route, 200, {
        id: '66666666-6666-4666-8666-666666666666',
        artifact_id: '55555555-5555-4555-8555-555555555555',
        version: '2.4.3', board: 'bread-compact-wifi-lcd', chip: 'esp32s3', partition: 'ota_0',
        file_size: 4, sha256: 'a'.repeat(64), force: false, published: true, created_at: '2026-08-22T00:00:00Z',
      })
    }
    return json(route, 500, { detail: `Unexpected mock request: ${method} ${path}` })
  })
}
