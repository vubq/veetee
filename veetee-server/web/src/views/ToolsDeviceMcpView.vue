<script setup lang="ts">
import {
  AlertCircle,
  AlertTriangle,
  Cpu,
  Globe,
  Play,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  Wrench,
} from '@lucide/vue'
import { onMounted, ref } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import {
  ApiError,
  confirmDeviceMcpCall,
  createEndpoint,
  deleteEndpoint,
  listAgents,
  listAgentPermissions,
  listDeviceMcpTools,
  listDevices,
  listEndpoints,
  prepareDeviceMcpCall,
  putAgentPermission,
  testToolCall,
  testToolsList,
  updateEndpoint,
  type AgentSummary,
  type DeviceMcpTool,
  type DeviceSummary,
  type ExternalEndpoint,
  type IntegrationPermission,
} from '@/api/controlPlane'

// Subtabs inside view: 'external' | 'device-mcp'
const subSection = ref<'external' | 'device-mcp'>('external')

const endpoints = ref<ExternalEndpoint[]>([])
const loadingEndpoints = ref(false)
const errorEndpoints = ref('')
const actionError = ref('')

// Endpoint dialog
const createEndpointOpen = ref(false)
const epName = ref('')
const epUrl = ref('')
const epAuthEnv = ref('')
const creatingEndpoint = ref(false)

// Endpoint test list / call
const testEndpointId = ref<string | null>(null)
const testTools = ref<Array<{ name: string; description: string }>>([])
const loadingTestTools = ref(false)
const testCallToolName = ref('')
const testCallArgs = ref('{}')
const testCallResult = ref<unknown | null>(null)
const testingCall = ref(false)

// Permissions
const agents = ref<AgentSummary[]>([])
const selectedAgentId = ref<string>('')
const permissions = ref<IntegrationPermission[]>([])
const loadingPermissions = ref(false)

// Device MCP
const devices = ref<DeviceSummary[]>([])
const loadingDevices = ref(false)
const selectedDevice = ref<DeviceSummary | null>(null)
const deviceTools = ref<DeviceMcpTool[]>([])
const deviceMcpSessionId = ref('')
const loadingDeviceTools = ref(false)
const selectedDeviceTool = ref<DeviceMcpTool | null>(null)

// Arguments editor for Device MCP
const deviceToolArgs = ref('{}')
const deviceToolResult = ref<{ content: unknown[]; is_error: boolean; truncated: boolean } | null>(null)
const executingMcp = ref(false)

// Explicit Confirmation Modal state
const confirmModalOpen = ref(false)
const pendingToken = ref<string>('')
const pendingExpiresIn = ref<number>(60)
const pendingToolName = ref<string>('')
const pendingArgsJson = ref<string>('')
const preparingMcp = ref(false)

async function loadExternalData() {
  loadingEndpoints.value = true
  errorEndpoints.value = ''
  actionError.value = ''
  try {
    const [epList, agentList] = await Promise.all([
      listEndpoints(),
      listAgents().catch(() => []),
    ])
    endpoints.value = epList
    agents.value = agentList
  } catch (err) {
    errorEndpoints.value = err instanceof Error ? err.message : 'Không thể tải tích hợp external endpoints.'
  } finally {
    loadingEndpoints.value = false
  }
}

async function handleCreateEndpoint() {
  if (!epName.value.trim() || !epUrl.value.trim()) return
  creatingEndpoint.value = true
  actionError.value = ''
  try {
    const created = await createEndpoint({
      name: epName.value.trim(),
      url: epUrl.value.trim(),
      auth_header_env: epAuthEnv.value.trim(),
      enabled: true,
    })
    endpoints.value.push(created)
    createEndpointOpen.value = false
    epName.value = ''
    epUrl.value = ''
    epAuthEnv.value = ''
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tạo tích hợp endpoint.'
  } finally {
    creatingEndpoint.value = false
  }
}

async function handleToggleEndpoint(ep: ExternalEndpoint) {
  actionError.value = ''
  try {
    const updated = await updateEndpoint(ep.id, {
      expected_version: ep.version,
      enabled: !ep.enabled,
    })
    const idx = endpoints.value.findIndex(e => e.id === ep.id)
    if (idx >= 0) endpoints.value[idx] = updated
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể cập nhật endpoint.'
  }
}

async function handleDeleteEndpoint(id: string) {
  if (!confirm('Bạn có chắc muốn xóa endpoint này?')) return
  actionError.value = ''
  try {
    await deleteEndpoint(id)
    endpoints.value = endpoints.value.filter(e => e.id !== id)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa endpoint.'
  }
}

async function handleTestListTools(epId: string) {
  if (!selectedAgentId.value) {
    actionError.value = 'Vui lòng chọn một trợ lý ở phần Phân quyền bên dưới trước khi chạy thử.'
    return
  }
  testEndpointId.value = epId
  loadingTestTools.value = true
  testTools.value = []
  testCallResult.value = null
  actionError.value = ''
  try {
    const res = await testToolsList(epId, selectedAgentId.value)
    testTools.value = res.tools
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Không có quyền truy cập tools/list cho agent này.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Kiểm tra tools/list thất bại.'
    }
  } finally {
    loadingTestTools.value = false
  }
}

async function handleTestCallTool(epId: string) {
  if (!selectedAgentId.value || !testCallToolName.value.trim()) return
  testingCall.value = true
  testCallResult.value = null
  actionError.value = ''
  let parsedArgs: Record<string, unknown> = {}
  try {
    parsedArgs = JSON.parse(testCallArgs.value || '{}')
  } catch {
    actionError.value = 'JSON Arguments không hợp lệ.'
    testingCall.value = false
    return
  }
  try {
    testCallResult.value = await testToolCall(epId, {
      agent_id: selectedAgentId.value,
      tool_name: testCallToolName.value.trim(),
      arguments: parsedArgs,
    })
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Không có quyền gọi tool này hoặc chưa được cấp phép.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Gọi thử công cụ thất bại.'
    }
  } finally {
    testingCall.value = false
  }
}

// Agent Permissions
async function onAgentSelect(agentId: string) {
  selectedAgentId.value = agentId
  if (!agentId) return
  loadingPermissions.value = true
  permissions.value = []
  try {
    permissions.value = await listAgentPermissions(agentId)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải phân quyền tích hợp.'
  } finally {
    loadingPermissions.value = false
  }
}

async function handleTogglePermission(epId: string, type: 'list' | 'call') {
  if (!selectedAgentId.value) return
  actionError.value = ''
  const current = permissions.value.find(p => p.endpoint_id === epId)
  const canList = type === 'list' ? !current?.can_list : (current?.can_list ?? false)
  const canCall = type === 'call' ? !current?.can_call : (current?.can_call ?? false)
  try {
    const updated = await putAgentPermission(selectedAgentId.value, epId, {
      can_list: canList,
      can_call: canCall,
    })
    const idx = permissions.value.findIndex(p => p.endpoint_id === epId)
    if (idx >= 0) permissions.value[idx] = updated
    else permissions.value.push(updated)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Cập nhật phân quyền thất bại.'
  }
}

// Device MCP
async function loadDeviceData() {
  loadingDevices.value = true
  actionError.value = ''
  try {
    devices.value = await listDevices()
    if (devices.value.length > 0 && !selectedDevice.value) {
      selectDevice(devices.value[0])
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải danh sách thiết bị.'
  } finally {
    loadingDevices.value = false
  }
}

async function selectDevice(dev: DeviceSummary) {
  selectedDevice.value = dev
  selectedDeviceTool.value = null
  deviceTools.value = []
  deviceMcpSessionId.value = ''
  deviceToolResult.value = null
  actionError.value = ''
}

async function handleFetchDeviceMcpTools() {
  if (!selectedDevice.value) return
  loadingDeviceTools.value = true
  deviceTools.value = []
  actionError.value = ''
  try {
    const res = await listDeviceMcpTools(selectedDevice.value.id)
    deviceMcpSessionId.value = res.session_id
    deviceTools.value = res.tools
    if (res.tools.length > 0) {
      selectDeviceTool(res.tools[0])
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Thiết bị đang ngoại tuyến hoặc phiên làm việc đã đóng.'
    } else if (err instanceof ApiError && err.status === 504) {
      actionError.value = 'Hết thời gian phản hồi từ thiết bị (Timeout).'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Không thể tải danh sách công cụ MCP từ thiết bị.'
    }
  } finally {
    loadingDeviceTools.value = false
  }
}

function selectDeviceTool(tool: DeviceMcpTool) {
  selectedDeviceTool.value = tool
  deviceToolArgs.value = '{}'
  deviceToolResult.value = null
}

// Two-step MCP call with explicit confirmation modal
async function handlePrepareMcpCall() {
  if (!selectedDevice.value || !selectedDeviceTool.value) return
  preparingMcp.value = true
  actionError.value = ''
  let parsedArgs: Record<string, unknown> = {}
  try {
    parsedArgs = JSON.parse(deviceToolArgs.value || '{}')
  } catch {
    actionError.value = 'JSON Arguments không hợp lệ.'
    preparingMcp.value = false
    return
  }

  try {
    const prep = await prepareDeviceMcpCall(selectedDevice.value.id, selectedDeviceTool.value.name, {
      session_id: deviceMcpSessionId.value,
      arguments: parsedArgs,
    })
    pendingToken.value = prep.confirmation_token
    pendingExpiresIn.value = prep.expires_in_seconds
    pendingToolName.value = prep.tool_name
    pendingArgsJson.value = JSON.stringify(parsedArgs, null, 2)
    confirmModalOpen.value = true
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Thiết bị đã ngắt kết nối hoặc không phản hồi.'
    } else if (err instanceof ApiError && err.status === 429) {
      actionError.value = 'Quá nhiều yêu cầu chuẩn bị chờ xác nhận. Vui lòng thử lại sau.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Chuẩn bị thực thi công cụ thất bại.'
    }
  } finally {
    preparingMcp.value = false
  }
}

async function handleConfirmMcpExecute() {
  if (!selectedDevice.value || !pendingToolName.value || !pendingToken.value) return
  executingMcp.value = true
  const tokenToUse = pendingToken.value
  actionError.value = ''
  confirmModalOpen.value = false
  // Immediately wipe state token so it cannot be re-read
  pendingToken.value = ''
  try {
    const res = await confirmDeviceMcpCall(selectedDevice.value.id, pendingToolName.value, tokenToUse)
    deviceToolResult.value = res
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Mã xác nhận đã hết hạn, không hợp lệ hoặc đã được sử dụng.'
    } else if (err instanceof ApiError && err.status === 504) {
      actionError.value = 'Thao tác thực thi công cụ thiết bị bị quá thời hạn (Timeout).'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Thực thi công cụ thất bại.'
    }
  } finally {
    executingMcp.value = false
    pendingToken.value = ''
  }
}

function cancelConfirmModal() {
  confirmModalOpen.value = false
  pendingToken.value = ''
  pendingToolName.value = ''
  pendingArgsJson.value = ''
}

onMounted(() => {
  loadExternalData()
  loadDeviceData()
})
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Tích hợp & thiết bị</h1>
        <p class="subtitle">Quản lý tích hợp công cụ ngoài (External HTTPS MCP) và điều khiển live MCP thiết bị.</p>
      </div>
      <div class="header-actions">
        <button
          class="secondary-button"
          :class="{ 'is-active': subSection === 'external' }"
          type="button"
          @click="subSection = 'external'"
        >
          <Globe :size="15" />
          <span>External Integrations</span>
        </button>
        <button
          class="secondary-button"
          :class="{ 'is-active': subSection === 'device-mcp' }"
          type="button"
          @click="subSection = 'device-mcp'"
        >
          <Cpu :size="15" />
          <span>Device MCP Tools</span>
        </button>
      </div>
    </div>

    <div v-if="actionError" class="alert-box is-error" role="alert">
      <AlertCircle :size="16" />
      <span>{{ actionError }}</span>
    </div>

    <!-- Part 1: External Endpoints -->
    <div v-if="subSection === 'external'" class="view-content">
      <div class="card">
        <div class="card-header">
          <h2>Outbound HTTPS MCP Endpoints</h2>
          <button class="primary-button compact" type="button" @click="createEndpointOpen = true">
            <Plus :size="14" />
            <span>Thêm Endpoint</span>
          </button>
        </div>

        <div v-if="loadingEndpoints" class="state-card loading-card compact">
          <RefreshCw :size="18" class="spin" />
          <p>Đang tải danh sách endpoints...</p>
        </div>

        <div v-else-if="endpoints.length === 0" class="empty-state">
          <Globe :size="28" />
          <p>Chưa có tích hợp external endpoint nào.</p>
        </div>

        <div v-else class="table-container">
          <table class="data-table">
            <thead>
              <tr>
                <th>Tên</th>
                <th>URL</th>
                <th>Auth Env Var</th>
                <th>Trạng thái</th>
                <th class="actions-col">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ep in endpoints" :key="ep.id">
                <td class="font-medium">{{ ep.name }}</td>
                <td class="font-mono text-sm">{{ ep.url }}</td>
                <td><span class="badge kind-badge">{{ ep.auth_header_env || 'None' }}</span></td>
                <td>
                  <span class="badge" :class="ep.enabled ? 'enabled-badge' : 'disabled-badge'">
                    {{ ep.enabled ? 'Đã bật' : 'Đã tắt' }}
                  </span>
                </td>
                <td class="actions-col">
                  <button class="secondary-button compact" type="button" @click="handleToggleEndpoint(ep)">
                    {{ ep.enabled ? 'Tắt' : 'Bật' }}
                  </button>
                  <button class="secondary-button compact" type="button" @click="handleTestListTools(ep.id)">
                    Test List
                  </button>
                  <button class="icon-button compact" type="button" title="Xóa" @click="handleDeleteEndpoint(ep.id)">
                    <Trash2 :size="14" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Agent Permissions for External Endpoints -->
      <div class="card agent-assign-card">
        <div class="card-header">
          <h2>Phân quyền Tích hợp cho Trợ lý (Default Deny Gate)</h2>
        </div>
        <div class="assign-body">
          <div class="form-group select-group">
            <label>Chọn trợ lý:</label>
            <select :value="selectedAgentId" class="text-input select-input" @change="onAgentSelect(($event.target as HTMLSelectElement).value)">
              <option value="">-- Chọn trợ lý --</option>
              <option v-for="ag in agents" :key="ag.id" :value="ag.id">{{ ag.name }}</option>
            </select>
          </div>

          <div v-if="selectedAgentId" class="permissions-wrap">
            <div v-if="loadingPermissions" class="state-card loading-card compact">
              <RefreshCw :size="16" class="spin" />
              <p>Đang tải quyền tích hợp...</p>
            </div>
            <div v-else class="table-container">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Endpoint</th>
                    <th>Quyền Xem (`tools/list`)</th>
                    <th>Quyền Gọi (`tools/call`)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="ep in endpoints" :key="ep.id">
                    <td><strong>{{ ep.name }}</strong></td>
                    <td>
                      <button
                        class="secondary-button compact"
                        :class="{ 'is-active': permissions.find(p => p.endpoint_id === ep.id)?.can_list }"
                        type="button"
                        @click="handleTogglePermission(ep.id, 'list')"
                      >
                        {{ permissions.find(p => p.endpoint_id === ep.id)?.can_list ? 'Đã cho phép' : 'Từ chối' }}
                      </button>
                    </td>
                    <td>
                      <button
                        class="secondary-button compact"
                        :class="{ 'is-active': permissions.find(p => p.endpoint_id === ep.id)?.can_call }"
                        type="button"
                        @click="handleTogglePermission(ep.id, 'call')"
                      >
                        {{ permissions.find(p => p.endpoint_id === ep.id)?.can_call ? 'Đã cho phép' : 'Từ chối' }}
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <!-- Tools Test Execution Box -->
      <div v-if="testEndpointId && testTools.length > 0" class="card search-test-card">
        <div class="card-header">
          <h2>Thử nghiệm Gọi công cụ (Test Tool Call)</h2>
        </div>
        <div class="test-call-form">
          <div class="form-group">
            <label>Chọn công cụ:</label>
            <select v-model="testCallToolName" class="text-input select-input">
              <option value="">-- Chọn tool --</option>
              <option v-for="t in testTools" :key="t.name" :value="t.name">{{ t.name }} - {{ t.description }}</option>
            </select>
          </div>
          <div class="form-group">
            <label>Tham số Arguments (JSON):</label>
            <textarea v-model="testCallArgs" class="text-input textarea-input font-mono" rows="3"></textarea>
          </div>
          <button
            class="primary-button"
            type="button"
            :disabled="testingCall || !testCallToolName"
            @click="handleTestCallTool(testEndpointId!)"
          >
            <Play :size="15" />
            <span>{{ testingCall ? 'Đang gọi...' : 'Thực thi gọi thử' }}</span>
          </button>
        </div>

        <div v-if="testCallResult" class="preview-output">
          <h4>Kết quả trả về:</h4>
          <pre class="font-mono text-sm result-pre">{{ JSON.stringify(testCallResult, null, 2) }}</pre>
        </div>
      </div>
    </div>

    <!-- Part 2: Device MCP Tools -->
    <div v-else-if="subSection === 'device-mcp'" class="view-content device-mcp-layout">
      <div class="mcp-grid">
        <!-- Device List Sidebar -->
        <div class="card device-select-card">
          <div class="card-header">
            <h2>Thiết bị</h2>
            <button class="icon-button compact" type="button" title="Tải lại thiết bị" @click="loadDeviceData">
              <RefreshCw :size="14" />
            </button>
          </div>

          <div v-if="loadingDevices" class="state-card loading-card compact">
            <RefreshCw :size="16" class="spin" />
            <p>Đang tải danh sách thiết bị...</p>
          </div>

          <div v-else-if="devices.length === 0" class="empty-state">
            <Cpu :size="28" />
            <p>Chưa có thiết bị nào được gắn.</p>
          </div>

          <div v-else class="device-menu">
            <button
              v-for="d in devices"
              :key="d.id"
              class="device-menu-item"
              :class="{ 'is-active': selectedDevice?.id === d.id }"
              type="button"
              @click="selectDevice(d)"
            >
              <div class="device-item-info">
                <strong>{{ d.alias || d.device_id }}</strong>
                <span class="online-pill" :class="{ 'is-online': d.online }">
                  {{ d.online ? 'Online' : 'Offline' }}
                </span>
              </div>
            </button>
          </div>
        </div>

        <!-- Selected Device MCP Tools -->
        <div class="card mcp-tools-card">
          <template v-if="selectedDevice">
            <div class="card-header">
              <h2>MCP Tools của Thiết bị: {{ selectedDevice.alias || selectedDevice.device_id }}</h2>
              <button
                class="primary-button compact"
                type="button"
                :disabled="loadingDeviceTools || !selectedDevice.online"
                @click="handleFetchDeviceMcpTools"
              >
                <RefreshCw :size="14" :class="{ 'spin': loadingDeviceTools }" />
                <span>Tải danh sách MCP Tools</span>
              </button>
            </div>

            <div v-if="!selectedDevice.online" class="alert-box is-warning">
              <AlertTriangle :size="16" />
              <span>Thiết bị đang offline. Cần có kết nối WebSocket trực tuyến để gọi MCP Tools.</span>
            </div>

            <div v-if="loadingDeviceTools" class="state-card loading-card compact">
              <RefreshCw :size="18" class="spin" />
              <p>Đang gọi device `tools/list`...</p>
            </div>

            <div v-else-if="deviceTools.length === 0" class="empty-state">
              <Wrench :size="28" />
              <p>Bấm "Tải danh sách MCP Tools" để lấy danh sách công cụ từ thiết bị.</p>
            </div>

            <div v-else class="tools-execution-wrap">
              <div class="tool-selector-bar">
                <label for="device-mcp-tool">Chọn công cụ MCP:</label>
                <select
                  id="device-mcp-tool"
                  :value="selectedDeviceTool?.name"
                  class="text-input select-input"
                  @change="selectDeviceTool(deviceTools.find(t => t.name === ($event.target as HTMLSelectElement).value)!)"
                >
                  <option v-for="t in deviceTools" :key="t.name" :value="t.name">
                    {{ t.name }} - {{ t.description || 'Không có mô tả' }}
                  </option>
                </select>
              </div>

              <div v-if="selectedDeviceTool" class="tool-editor">
                <p class="tool-desc"><strong>Mô tả:</strong> {{ selectedDeviceTool.description || 'N/A' }}</p>

                <div class="form-group">
                  <label for="mcp-args">Tham số Arguments (JSON):</label>
                  <textarea id="mcp-args" v-model="deviceToolArgs" class="text-input textarea-input font-mono" rows="4"></textarea>
                </div>

                <button
                  class="primary-button"
                  type="button"
                  data-testid="device-mcp-call-btn"
                  :disabled="preparingMcp || executingMcp || !selectedDevice.online"
                  @click="handlePrepareMcpCall"
                >
                  <Play :size="16" />
                  <span>{{ preparingMcp ? 'Đang chuẩn bị...' : 'Gọi công cụ' }}</span>
                </button>
              </div>

              <!-- Execution Output -->
              <div v-if="deviceToolResult" class="preview-output">
                <h4>Kết quả thực thi từ thiết bị:</h4>
                <div class="result-badges">
                  <span class="badge" :class="deviceToolResult.is_error ? 'disabled-badge' : 'enabled-badge'">
                    {{ deviceToolResult.is_error ? 'Lỗi' : 'Thành công' }}
                  </span>
                  <span v-if="deviceToolResult.truncated" class="badge kind-badge">Bị cắt bớt (Truncated)</span>
                </div>
                <pre class="font-mono text-sm result-pre">{{ JSON.stringify(deviceToolResult.content, null, 2) }}</pre>
              </div>
            </div>
          </template>

          <div v-else class="empty-state">
            <Cpu :size="32" />
            <p>Vui lòng chọn thiết bị từ danh sách bên trái.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Endpoint Dialog -->
    <UiDialog :open="createEndpointOpen" title="Thêm External Endpoint" @close="createEndpointOpen = false">
      <form class="dialog-form" @submit.prevent="handleCreateEndpoint">
        <div class="form-group">
          <label for="ep-name">Tên Endpoint <span class="required">*</span></label>
          <input id="ep-name" v-model="epName" type="text" class="text-input" required placeholder="Ví dụ: Weather API Endpoint" />
        </div>
        <div class="form-group">
          <label for="ep-url">URL Outbound HTTPS <span class="required">*</span></label>
          <input id="ep-url" v-model="epUrl" type="url" class="text-input font-mono" required placeholder="https://api.example.test/mcp" />
        </div>
        <div class="form-group">
          <label for="ep-env">Tên biến môi trường chứa Auth Secret (Tùy chọn)</label>
          <input id="ep-env" v-model="epAuthEnv" type="text" class="text-input font-mono" placeholder="WEATHER_API_KEY" />
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="createEndpointOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="creatingEndpoint || !epName.trim() || !epUrl.trim()" @click="handleCreateEndpoint">
          {{ creatingEndpoint ? 'Đang tạo...' : 'Tạo mới' }}
        </button>
      </template>
    </UiDialog>

    <!-- Explicit Confirmation Modal for Device MCP Calls -->
    <UiDialog :open="confirmModalOpen" title="Xác nhận thực thi công cụ thiết bị" size="medium" @close="cancelConfirmModal">
      <div class="confirm-modal-body" data-testid="mcp-confirm-modal">
        <div class="alert-box is-warning">
          <AlertTriangle :size="20" />
          <div>
            <strong>CẢNH BÁO THAO TÁC THỰC THI TRÊN THIẾT BỊ THỰC</strong>
            <p>Thao tác này sẽ gửi lệnh đến thiết bị <strong>{{ selectedDevice?.alias || selectedDevice?.device_id }}</strong> với các tham số bên dưới.</p>
          </div>
        </div>

        <div class="confirm-details">
          <p><strong>Công cụ:</strong> <code>{{ pendingToolName }}</code></p>
          <p><strong>Thời gian hiệu lực mã xác nhận:</strong> {{ pendingExpiresIn }} giây</p>
          <div class="args-preview">
            <label>Tham số sẽ gửi:</label>
            <pre class="font-mono text-sm result-pre">{{ pendingArgsJson }}</pre>
          </div>
        </div>
      </div>
      <template #footer>
        <button class="secondary-button" type="button" @click="cancelConfirmModal">Hủy bỏ</button>
        <button
          class="primary-button"
          type="button"
          data-testid="mcp-confirm-submit-btn"
          :disabled="executingMcp"
          @click="handleConfirmMcpExecute"
        >
          <Shield :size="16" />
          <span>{{ executingMcp ? 'Đang thực thi...' : 'Xác nhận gọi ngay' }}</span>
        </button>
      </template>
    </UiDialog>
  </div>
</template>
