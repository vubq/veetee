<script setup lang="ts">
import { AlertCircle, ArrowRight, Plus, RefreshCw, Sliders, Trash2 } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import {
  createCorrectionRule,
  createCorrectionSet,
  deleteAgentContextProvider,
  deleteCorrectionRule,
  deleteCorrectionSet,
  listAgentContextProviders,
  listAgents,
  listCorrectionRules,
  listCorrectionSets,
  previewCorrectionSet,
  putAgentContextProvider,
  updateCorrectionSet,
  type AgentSummary,
  type ContextProviderConfig,
  type CorrectionPreviewResponse,
  type CorrectionRule,
  type CorrectionSet,
} from '@/api/controlPlane'

const sets = ref<CorrectionSet[]>([])
const loading = ref(true)
const error = ref('')
const actionError = ref('')

const selectedSetId = ref<string | null>(null)
const selectedSet = ref<CorrectionSet | null>(null)
const rules = ref<CorrectionRule[]>([])
const loadingRules = ref(false)

// Set create/edit dialog
const createSetOpen = ref(false)
const setName = ref('')
const creatingSet = ref(false)

// Rule create dialog
const createRuleOpen = ref(false)
const ruleOrdinal = ref(1)
const ruleType = ref<'exact' | 'phrase'>('exact')
const rulePattern = ref('')
const ruleReplacement = ref('')
const ruleCaseSensitive = ref(false)
const creatingRule = ref(false)

// Preview
const previewText = ref('Xin chao, chau ten la Veetee.')
const previewResult = ref<CorrectionPreviewResponse | null>(null)
const previewing = ref(false)

// Context Providers
const agents = ref<AgentSummary[]>([])
const selectedAgentId = ref<string>('')
const contextProviders = ref<ContextProviderConfig[]>([])
const loadingContextProviders = ref(false)

// Edit provider config
const editProviderOpen = ref(false)
const providerType = ref<string>('runtime')
const providerEnabled = ref(true)
const providerOrdinal = ref(1)
const providerTimeout = ref(1500)
const providerTtl = ref(300)
const savingProvider = ref(false)

async function loadSets() {
  loading.value = true
  error.value = ''
  actionError.value = ''
  try {
    const [setList, agentList] = await Promise.all([
      listCorrectionSets(),
      listAgents().catch(() => []),
    ])
    sets.value = setList
    agents.value = agentList
    if (setList.length > 0 && !selectedSetId.value) {
      selectSet(setList[0])
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Không thể tải bộ hiệu chỉnh.'
  } finally {
    loading.value = false
  }
}

async function selectSet(setObj: CorrectionSet) {
  selectedSetId.value = setObj.id
  selectedSet.value = setObj
  loadingRules.value = true
  rules.value = []
  actionError.value = ''
  try {
    rules.value = await listCorrectionRules(setObj.id)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải quy tắc hiệu chỉnh.'
  } finally {
    loadingRules.value = false
  }
}

async function handleCreateSet() {
  if (!setName.value.trim()) return
  creatingSet.value = true
  actionError.value = ''
  try {
    const created = await createCorrectionSet({ name: setName.value.trim(), enabled: true })
    sets.value.push(created)
    createSetOpen.value = false
    setName.value = ''
    selectSet(created)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tạo bộ hiệu chỉnh.'
  } finally {
    creatingSet.value = false
  }
}

async function handleToggleSet(setObj: CorrectionSet) {
  actionError.value = ''
  try {
    const updated = await updateCorrectionSet(setObj.id, {
      enabled: !setObj.enabled,
      expected_version: setObj.version,
    })
    const idx = sets.value.findIndex(s => s.id === setObj.id)
    if (idx >= 0) sets.value[idx] = updated
    if (selectedSetId.value === setObj.id) selectedSet.value = updated
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể cập nhật bộ hiệu chỉnh.'
  }
}

async function handleDeleteSet(id: string) {
  if (!confirm('Bạn có chắc muốn xóa bộ hiệu chỉnh này?')) return
  actionError.value = ''
  try {
    await deleteCorrectionSet(id)
    sets.value = sets.value.filter(s => s.id !== id)
    if (selectedSetId.value === id) {
      selectedSetId.value = sets.value[0]?.id || null
      selectedSet.value = sets.value[0] || null
      if (selectedSet.value) selectSet(selectedSet.value)
      else rules.value = []
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa bộ hiệu chỉnh.'
  }
}

async function handleCreateRule() {
  if (!selectedSet.value || !rulePattern.value.trim()) return
  creatingRule.value = true
  actionError.value = ''
  try {
    const newRule = await createCorrectionRule(selectedSet.value.id, {
      ordinal: ruleOrdinal.value,
      rule_type: ruleType.value,
      pattern: rulePattern.value.trim(),
      replacement: ruleReplacement.value,
      case_sensitive: ruleCaseSensitive.value,
      enabled: true,
      expected_set_version: selectedSet.value.version,
    })
    rules.value.push(newRule)
    createRuleOpen.value = false
    rulePattern.value = ''
    ruleReplacement.value = ''
    // reload set to get updated version
    const updatedSets = await listCorrectionSets()
    sets.value = updatedSets
    selectedSet.value = updatedSets.find(s => s.id === selectedSet.value?.id) || selectedSet.value
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tạo quy tắc.'
  } finally {
    creatingRule.value = false
  }
}

async function handleDeleteRule(ruleId: string) {
  actionError.value = ''
  try {
    await deleteCorrectionRule(ruleId)
    rules.value = rules.value.filter(r => r.id !== ruleId)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa quy tắc.'
  }
}

async function handlePreview() {
  if (!selectedSetId.value || !previewText.value.trim()) return
  previewing.value = true
  previewResult.value = null
  actionError.value = ''
  try {
    previewResult.value = await previewCorrectionSet(selectedSetId.value, previewText.value.trim())
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Xem trước thất bại.'
  } finally {
    previewing.value = false
  }
}

// Context providers
async function onAgentSelect(agentId: string) {
  selectedAgentId.value = agentId
  if (!agentId) return
  loadingContextProviders.value = true
  contextProviders.value = []
  try {
    contextProviders.value = await listAgentContextProviders(agentId)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải nhà cung cấp ngữ cảnh.'
  } finally {
    loadingContextProviders.value = false
  }
}

function openEditProvider(provider: ContextProviderConfig) {
  providerType.value = provider.provider_type
  providerEnabled.value = provider.enabled
  providerOrdinal.value = provider.ordinal
  providerTimeout.value = provider.timeout_ms
  providerTtl.value = provider.cache_ttl_seconds
  editProviderOpen.value = true
}

async function handleSaveProvider() {
  if (!selectedAgentId.value) return
  savingProvider.value = true
  actionError.value = ''
  try {
    const saved = await putAgentContextProvider(selectedAgentId.value, providerType.value, {
      enabled: providerEnabled.value,
      ordinal: providerOrdinal.value,
      timeout_ms: providerTimeout.value,
      cache_ttl_seconds: providerTtl.value,
      config: {},
    })
    const idx = contextProviders.value.findIndex(cp => cp.provider_type === providerType.value)
    if (idx >= 0) contextProviders.value[idx] = saved
    else contextProviders.value.push(saved)
    editProviderOpen.value = false
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể lưu cấu hình nhà cung cấp ngữ cảnh.'
  } finally {
    savingProvider.value = false
  }
}

async function handleDeleteProvider(pType: string) {
  if (!selectedAgentId.value) return
  actionError.value = ''
  try {
    await deleteAgentContextProvider(selectedAgentId.value, pType)
    contextProviders.value = contextProviders.value.filter(cp => cp.provider_type !== pType)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa cấu hình.'
  }
}

onMounted(() => loadSets())
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Hiệu chỉnh & ngữ cảnh</h1>
        <p class="subtitle">Quản lý các bộ quy tắc hiệu chỉnh văn bản ASR/LLM và cấu hình Context Provider per-agent.</p>
      </div>
      <div class="header-actions">
        <button class="primary-button" type="button" @click="createSetOpen = true">
          <Plus :size="16" />
          <span>Tạo bộ hiệu chỉnh</span>
        </button>
        <button class="icon-button" type="button" title="Tải lại" :disabled="loading" @click="loadSets">
          <RefreshCw :size="16" :class="{ 'spin': loading }" />
        </button>
      </div>
    </div>

    <div v-if="loading" class="state-card loading-card">
      <RefreshCw :size="24" class="spin" />
      <p>Đang tải bộ hiệu chỉnh...</p>
    </div>

    <div v-else-if="error" class="state-card error-card">
      <AlertCircle :size="24" />
      <p>{{ error }}</p>
      <button class="primary-button" type="button" @click="loadSets">Thử lại</button>
    </div>

    <div v-else class="view-content corrections-layout">
      <div v-if="actionError" class="alert-box is-error" role="alert">
        <AlertCircle :size="16" />
        <span>{{ actionError }}</span>
      </div>

      <div class="corrections-grid">
        <!-- Sets Sidebar -->
        <div class="card sets-card">
          <div class="card-header">
            <h2>Bộ hiệu chỉnh</h2>
            <span class="badge">{{ sets.length }}</span>
          </div>

          <div v-if="sets.length === 0" class="empty-state">
            <Sliders :size="28" />
            <p>Chưa có bộ hiệu chỉnh nào.</p>
          </div>

          <div v-else class="sets-menu">
            <button
              v-for="s in sets"
              :key="s.id"
              class="set-menu-item"
              :class="{ 'is-active': selectedSetId === s.id }"
              type="button"
              @click="selectSet(s)"
            >
              <div class="set-item-info">
                <strong>{{ s.name }}</strong>
                <span class="badge" :class="s.enabled ? 'enabled-badge' : 'disabled-badge'">
                  v{{ s.version }} - {{ s.enabled ? 'Đã bật' : 'Tắt' }}
                </span>
              </div>
              <div class="set-item-actions">
                <button
                  class="secondary-button compact"
                  type="button"
                  @click.stop="handleToggleSet(s)"
                >
                  {{ s.enabled ? 'Tắt' : 'Bật' }}
                </button>
                <button
                  class="icon-button compact"
                  type="button"
                  title="Xóa"
                  @click.stop="handleDeleteSet(s.id)"
                >
                  <Trash2 :size="14" />
                </button>
              </div>
            </button>
          </div>
        </div>

        <!-- Rules & Preview Card -->
        <div class="card rules-card">
          <template v-if="selectedSet">
            <div class="card-header">
              <h2>Quy tắc trong bộ: {{ selectedSet.name }}</h2>
              <button class="primary-button compact" type="button" @click="createRuleOpen = true">
                <Plus :size="14" />
                <span>Thêm quy tắc</span>
              </button>
            </div>

            <div v-if="loadingRules" class="state-card loading-card compact">
              <RefreshCw :size="18" class="spin" />
              <p>Đang tải quy tắc...</p>
            </div>

            <div v-else-if="rules.length === 0" class="empty-state">
              <p>Chưa có quy tắc nào trong bộ này.</p>
            </div>

            <div v-else class="table-container">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Thứ tự</th>
                    <th>Loại</th>
                    <th>Pattern</th>
                    <th>Thay thế</th>
                    <th>Phân biệt hoa/thường</th>
                    <th class="actions-col">Xóa</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in rules" :key="r.id">
                    <td>{{ r.ordinal }}</td>
                    <td><span class="badge kind-badge">{{ r.rule_type }}</span></td>
                    <td class="font-mono">{{ r.pattern }}</td>
                    <td class="font-mono">{{ r.replacement }}</td>
                    <td>{{ r.case_sensitive ? 'Có' : 'Không' }}</td>
                    <td class="actions-col">
                      <button class="icon-button compact" type="button" title="Xóa" @click="handleDeleteRule(r.id)">
                        <Trash2 :size="14" />
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Preview Execution -->
            <div class="preview-section">
              <h3>Chạy thử hiệu chỉnh (Preview)</h3>
              <div class="form-row">
                <input
                  v-model="previewText"
                  type="text"
                  class="text-input"
                  placeholder="Nhập đoạn văn bản cần thử nghiệm..."
                  @keydown.enter="handlePreview"
                />
                <button class="primary-button" type="button" :disabled="previewing || !previewText.trim()" @click="handlePreview">
                  <span>{{ previewing ? 'Đang chạy...' : 'Chạy thử' }}</span>
                </button>
              </div>

              <div v-if="previewResult" class="preview-output">
                <div class="preview-comparison">
                  <div class="prev-box">
                    <span class="label">Văn bản gốc:</span>
                    <p>{{ previewResult.original_text }}</p>
                  </div>
                  <ArrowRight :size="20" class="arrow-icon" />
                  <div class="prev-box is-result">
                    <span class="label">Văn bản sau hiệu chỉnh:</span>
                    <p>{{ previewResult.corrected_text }}</p>
                  </div>
                </div>
                <div class="applied-rules-summary">
                  <span>Đã áp dụng {{ previewResult.applied_rules.length }} quy tắc.</span>
                </div>
              </div>
            </div>
          </template>

          <div v-else class="empty-state">
            <Sliders :size="32" />
            <p>Vui lòng chọn hoặc tạo bộ hiệu chỉnh.</p>
          </div>
        </div>
      </div>

      <!-- Agent Context Providers Governance Section -->
      <div class="card context-providers-card">
        <div class="card-header">
          <h2>Nhà cung cấp ngữ cảnh của Trợ lý (Context Providers)</h2>
        </div>
        <div class="assign-body">
          <div class="form-group select-group">
            <label>Chọn trợ lý:</label>
            <select :value="selectedAgentId" class="text-input select-input" @change="onAgentSelect(($event.target as HTMLSelectElement).value)">
              <option value="">-- Chọn trợ lý --</option>
              <option v-for="ag in agents" :key="ag.id" :value="ag.id">{{ ag.name }}</option>
            </select>
          </div>

          <div v-if="selectedAgentId" class="providers-table-wrap">
            <div v-if="loadingContextProviders" class="state-card loading-card compact">
              <RefreshCw :size="16" class="spin" />
              <p>Đang tải context providers...</p>
            </div>
            <div v-else class="table-container">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Loại Context Provider</th>
                    <th>Trạng thái</th>
                    <th>Thứ tự (Ordinal)</th>
                    <th>Timeout (ms)</th>
                    <th>Cache TTL (s)</th>
                    <th class="actions-col">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="cp in contextProviders" :key="cp.provider_type">
                    <td class="font-medium">{{ cp.provider_type }}</td>
                    <td>
                      <span class="badge" :class="cp.enabled ? 'enabled-badge' : 'disabled-badge'">
                        {{ cp.enabled ? 'Bật' : 'Tắt' }}
                      </span>
                    </td>
                    <td>{{ cp.ordinal }}</td>
                    <td>{{ cp.timeout_ms }}ms</td>
                    <td>{{ cp.cache_ttl_seconds }}s</td>
                    <td class="actions-col">
                      <button class="secondary-button compact" type="button" @click="openEditProvider(cp)">Sửa</button>
                      <button class="icon-button compact" type="button" title="Xóa" @click="handleDeleteProvider(cp.provider_type)">
                        <Trash2 :size="14" />
                      </button>
                    </td>
                  </tr>
                  <tr v-if="contextProviders.length === 0">
                    <td colspan="6" class="text-center text-muted">Chưa cấu hình context provider nào cho trợ lý này.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Set Dialog -->
    <UiDialog :open="createSetOpen" title="Tạo bộ hiệu chỉnh mới" @close="createSetOpen = false">
      <form class="dialog-form" @submit.prevent="handleCreateSet">
        <div class="form-group">
          <label for="set-name">Tên bộ hiệu chỉnh <span class="required">*</span></label>
          <input id="set-name" v-model="setName" type="text" class="text-input" required placeholder="Ví dụ: Quy tắc đọc tên riêng ASR" />
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="createSetOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="creatingSet || !setName.trim()" @click="handleCreateSet">
          {{ creatingSet ? 'Đang tạo...' : 'Tạo mới' }}
        </button>
      </template>
    </UiDialog>

    <!-- Create Rule Dialog -->
    <UiDialog :open="createRuleOpen" title="Thêm quy tắc hiệu chỉnh" @close="createRuleOpen = false">
      <form class="dialog-form" @submit.prevent="handleCreateRule">
        <div class="form-group">
          <label for="r-ordinal">Thứ tự ưu tiên</label>
          <input id="r-ordinal" v-model.number="ruleOrdinal" type="number" min="1" class="text-input" />
        </div>
        <div class="form-group">
          <label for="r-type">Loại quy tắc</label>
          <select id="r-type" v-model="ruleType" class="text-input select-input">
            <option value="exact">Chính xác (Exact)</option>
            <option value="phrase">Cụm từ trong câu</option>
          </select>
        </div>
        <div class="form-group">
          <label for="r-pattern">Pattern cần thay thế <span class="required">*</span></label>
          <input id="r-pattern" v-model="rulePattern" type="text" class="text-input font-mono" required placeholder="Ví dụ: Vietee" />
        </div>
        <div class="form-group">
          <label for="r-rep">Thay thế bằng</label>
          <input id="r-rep" v-model="ruleReplacement" type="text" class="text-input font-mono" placeholder="Ví dụ: Veetee" />
        </div>
        <div class="form-group checkbox-group">
          <label><input v-model="ruleCaseSensitive" type="checkbox" /> Phân biệt hoa thường</label>
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="createRuleOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="creatingRule || !rulePattern.trim()" @click="handleCreateRule">
          {{ creatingRule ? 'Đang tạo...' : 'Thêm quy tắc' }}
        </button>
      </template>
    </UiDialog>

    <!-- Edit Context Provider Dialog -->
    <UiDialog :open="editProviderOpen" title="Cấu hình Context Provider" @close="editProviderOpen = false">
      <form class="dialog-form" @submit.prevent="handleSaveProvider">
        <div class="form-group">
          <label>Loại provider: <strong>{{ providerType }}</strong></label>
        </div>
        <div class="form-group checkbox-group">
          <label><input v-model="providerEnabled" type="checkbox" /> Kích hoạt</label>
        </div>
        <div class="form-group">
          <label for="cp-ord">Thứ tự ưu tiên</label>
          <input id="cp-ord" v-model.number="providerOrdinal" type="number" min="1" class="text-input" />
        </div>
        <div class="form-group">
          <label for="cp-timeout">Timeout (ms)</label>
          <input id="cp-timeout" v-model.number="providerTimeout" type="number" min="100" class="text-input" />
        </div>
        <div class="form-group">
          <label for="cp-ttl">Cache TTL (giây)</label>
          <input id="cp-ttl" v-model.number="providerTtl" type="number" min="0" class="text-input" />
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="editProviderOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="savingProvider" @click="handleSaveProvider">
          {{ savingProvider ? 'Đang lưu...' : 'Lưu thay đổi' }}
        </button>
      </template>
    </UiDialog>
  </div>
</template>
