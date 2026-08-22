<script setup lang="ts">
import {
  AlertCircle,
  RefreshCw,
} from '@lucide/vue'
import { onMounted, ref } from 'vue'

import RoleGate from '@/components/RoleGate.vue'
import UiDialog from '@/components/UiDialog.vue'
import {
  AdminUser,
  ApiError,
  getMyQuota,
  getUserQuota,
  listSettings,
  listUsers,
  QuotaEffective,
  SettingItem,
  updateSetting,
  updateUserQuota,
} from '@/api/controlPlane'

// Settings
const settingsList = ref<SettingItem[]>([])
const loadingSettings = ref(true)
const errorSettings = ref('')
const settingsForbidden = ref(false)

// Edit Setting dialog
const editSettingOpen = ref(false)
const editSettingKey = ref('')
const editSettingValue = ref('')
const savingSetting = ref(false)

// Own Quota
const ownQuota = ref<QuotaEffective | null>(null)
const loadingOwnQuota = ref(true)
const errorOwnQuota = ref('')

// Admin User Quota Management
const users = ref<AdminUser[]>([])
const selectedUserId = ref<string>('')
const userQuotaObj = ref<QuotaEffective | null>(null)
const loadingUserQuota = ref(false)
const adminQuotaForbidden = ref(false)
const savingQuota = ref(false)

// Quota edit form values (null khi chưa có policy riêng; dùng effective limit làm gợi ý)
const qEnabled = ref(true)
const qLlmTokens = ref<number | null>(null)
const qTtsChars = ref<number | null>(null)
const qToolCalls = ref<number | null>(null)
const qRagBytes = ref<number | null>(null)

// Nhãn hiển thị cho từng metric quota theo key backend.
const quotaMetrics = [
  { key: 'llm_tokens_day', label: 'LLM Tokens / ngày' },
  { key: 'tts_chars_day', label: 'TTS Characters / ngày' },
  { key: 'tool_calls_minute', label: 'Tool Calls / phút' },
  { key: 'rag_bytes_month', label: 'RAG Bytes / tháng' },
] as const

function metricPercent(metric: { used: number; limit: number | null }): number {
  if (metric.limit === null || metric.limit <= 0) return 0
  return Math.min(100, (metric.used / metric.limit) * 100)
}

function formatMetricValue(value: number): string {
  return value.toLocaleString('vi-VN')
}

function formatMetricLimit(value: number | null): string {
  return value === null ? 'Không giới hạn' : formatMetricValue(value)
}

function settingDisplayValue(value: unknown): string {
  if (value === null) return '(null — dùng mặc định hệ thống)'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function settingType(value: unknown): string {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'array'
  return typeof value
}

const actionError = ref('')

async function loadData() {
  actionError.value = ''
  loadSettings()
  loadOwnQuotaData()
  loadUserList()
}

async function loadSettings() {
  loadingSettings.value = true
  errorSettings.value = ''
  settingsForbidden.value = false
  try {
    settingsList.value = await listSettings()
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      settingsForbidden.value = true
    } else {
      errorSettings.value = err instanceof Error ? err.message : 'Không thể tải cài đặt hệ thống.'
    }
  } finally {
    loadingSettings.value = false
  }
}

async function loadOwnQuotaData() {
  loadingOwnQuota.value = true
  errorOwnQuota.value = ''
  try {
    ownQuota.value = await getMyQuota()
  } catch (err) {
    errorOwnQuota.value = err instanceof Error ? err.message : 'Không thể tải quota cá nhân.'
  } finally {
    loadingOwnQuota.value = false
  }
}

async function loadUserList() {
  try {
    const res = await listUsers({ limit: 50 })
    users.value = res.items
  } catch {
    // Non-admin will fail, ignore
  }
}

function openEditSetting(s: SettingItem) {
  editSettingKey.value = s.key
  editSettingValue.value = settingDisplayValue(s.value)
  editSettingOpen.value = true
}

async function handleSaveSetting() {
  savingSetting.value = true
  actionError.value = ''
  // Backend không trả schema_type; suy luận từ giá trị hiện tại:
  // boolean giữ nguyên qua checkbox, còn lại parse JSON (số, null, object).
  const current = settingsList.value.find(s => s.key === editSettingKey.value)
  let parsedValue: unknown = editSettingValue.value
  if (typeof current?.value === 'boolean') {
    parsedValue = current.value
  } else {
    const raw = editSettingValue.value.trim()
    if (raw === '(null — dùng mặc định hệ thống)') {
      parsedValue = null
    } else {
      try {
        parsedValue = JSON.parse(raw)
      } catch {
        actionError.value = 'Giá trị không hợp lệ: hãy nhập số nguyên, true/false hoặc null.'
        savingSetting.value = false
        return
      }
    }
  }
  try {
    const updated = await updateSetting(editSettingKey.value, {
      value: parsedValue,
      expected_version: current?.version ?? 1,
    })
    const idx = settingsList.value.findIndex(s => s.key === updated.key)
    if (idx >= 0) settingsList.value[idx] = updated
    editSettingOpen.value = false
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để thay đổi cài đặt hệ thống.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Xung đột phiên bản: Cài đặt đã bị thay đổi bởi yêu cầu khác.'
      await loadSettings()
    } else {
      actionError.value = err instanceof Error ? err.message : 'Cập nhật cài đặt thất bại.'
    }
  } finally {
    savingSetting.value = false
  }
}

async function onSelectUserQuota(uId: string) {
  selectedUserId.value = uId
  if (!uId) {
    userQuotaObj.value = null
    return
  }
  loadingUserQuota.value = true
  adminQuotaForbidden.value = false
  userQuotaObj.value = null
  actionError.value = ''
  try {
    const qRes = await getUserQuota(uId)
    userQuotaObj.value = qRes
    qEnabled.value = qRes.enabled
    // Policy riêng có thể null: dùng effective limit (gồm default hệ thống) làm gợi ý form.
    qLlmTokens.value = qRes.policy?.llm_tokens_per_day ?? qRes.metrics.llm_tokens_day.limit
    qTtsChars.value = qRes.policy?.tts_chars_per_day ?? qRes.metrics.tts_chars_day.limit
    qToolCalls.value = qRes.policy?.tool_calls_per_minute ?? qRes.metrics.tool_calls_minute.limit
    qRagBytes.value = qRes.policy?.rag_bytes_per_month ?? qRes.metrics.rag_bytes_month.limit
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      adminQuotaForbidden.value = true
    } else {
      actionError.value = err instanceof Error ? err.message : 'Không thể tải quota của người dùng.'
    }
  } finally {
    loadingUserQuota.value = false
  }
}

async function handleSaveUserQuota() {
  if (!selectedUserId.value || !userQuotaObj.value) return
  savingQuota.value = true
  actionError.value = ''
  try {
    const updatedPolicy = await updateUserQuota(selectedUserId.value, {
      expected_version: userQuotaObj.value.policy?.version ?? 1,
      enabled: qEnabled.value,
      llm_tokens_per_day: qLlmTokens.value ?? 0,
      tts_chars_per_day: qTtsChars.value ?? 0,
      tool_calls_per_minute: qToolCalls.value ?? 0,
      rag_bytes_per_month: qRagBytes.value ?? 0,
    })
    // Lưu xong tải lại effective quota để usage/limit hiển thị nhất quán.
    await onSelectUserQuota(selectedUserId.value)
    void updatedPolicy
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để thay đổi quota của người dùng.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Xung đột phiên bản: Quota đã được thay đổi bởi yêu cầu khác.'
      await onSelectUserQuota(selectedUserId.value)
    } else {
      actionError.value = err instanceof Error ? err.message : 'Cập nhật quota thất bại.'
    }
  } finally {
    savingQuota.value = false
  }
}

onMounted(() => loadData())
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Cài đặt & Quota</h1>
        <p class="subtitle">Quản lý tham số hệ thống và chính sách giới hạn tài nguyên (Quota Governance).</p>
      </div>
      <button class="icon-button" type="button" title="Tải lại" :disabled="loadingSettings" @click="loadData">
        <RefreshCw :size="16" :class="{ 'spin': loadingSettings }" />
      </button>
    </div>

    <div v-if="actionError" class="alert-box is-error" role="alert">
      <AlertCircle :size="16" />
      <span>{{ actionError }}</span>
    </div>

    <div class="view-content settings-quota-layout">
      <!-- Own Quota Section (Always Available) -->
      <div class="card own-quota-card">
        <div class="card-header">
          <h2>Quota cá nhân của bạn (Own Usage)</h2>
          <span v-if="ownQuota" class="badge" :class="ownQuota.enabled ? 'kind-badge' : 'disabled-badge'">
            {{ ownQuota.enabled ? 'Đang áp dụng' : 'Đang tắt' }}
          </span>
        </div>

        <div v-if="loadingOwnQuota" class="state-card loading-card compact">
          <RefreshCw :size="18" class="spin" />
          <p>Đang tải quota cá nhân...</p>
        </div>

        <div v-else-if="errorOwnQuota" class="state-card error-card compact">
          <AlertCircle :size="18" />
          <p>{{ errorOwnQuota }}</p>
        </div>

        <div v-else-if="ownQuota" class="quota-stats-grid">
          <div v-for="metric in quotaMetrics" :key="metric.key" class="quota-stat-box">
            <span class="label">{{ metric.label }}</span>
            <div class="stat-values">
              <strong>{{ formatMetricValue(ownQuota.metrics[metric.key].used) }}</strong>
              <span> / {{ formatMetricLimit(ownQuota.metrics[metric.key].limit) }}</span>
            </div>
            <div class="progress-bar">
              <div
                class="progress-fill"
                :style="{ width: `${metricPercent(ownQuota.metrics[metric.key])}%` }"
              ></div>
            </div>
          </div>
        </div>
      </div>

      <!-- Section 1: System Settings -->
      <div class="card settings-section-card">
        <div class="card-header">
          <h2>Cài đặt hệ thống (Admin System Settings)</h2>
        </div>

        <RoleGate v-if="settingsForbidden" />

        <div v-else-if="loadingSettings" class="state-card loading-card compact">
          <RefreshCw :size="18" class="spin" />
          <p>Đang tải cài đặt hệ thống...</p>
        </div>

        <div v-else-if="errorSettings" class="state-card error-card compact">
          <AlertCircle :size="18" />
          <p>{{ errorSettings }}</p>
        </div>

        <div v-else class="table-container">
          <table class="data-table">
            <thead>
              <tr>
                <th>Khóa (Key)</th>
                <th>Giá trị hiện tại</th>
                <th>Kiểu dữ liệu</th>
                <th class="actions-col">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in settingsList" :key="s.key">
                <td class="font-medium font-mono">{{ s.key }}</td>
                <td class="font-mono text-sm">{{ typeof s.value === 'object' ? JSON.stringify(s.value) : String(s.value) }}</td>
                <td><span class="badge kind-badge">{{ settingType(s.value) }}</span></td>
                <td class="actions-col">
                  <button class="secondary-button compact" type="button" @click="openEditSetting(s)">
                    Sửa
                  </button>
                </td>
              </tr>
              <tr v-if="settingsList.length === 0">
                <td colspan="4" class="text-center text-muted">Chưa có cài đặt hệ thống nào.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Section 2: Quota Administration for Users -->
      <div class="card quota-admin-card">
        <div class="card-header">
          <h2>Quản trị Quota người dùng (User Quotas)</h2>
        </div>

        <div class="assign-body">
          <div class="form-group select-group">
            <label>Chọn người dùng:</label>
            <select :value="selectedUserId" class="text-input select-input" @change="onSelectUserQuota(($event.target as HTMLSelectElement).value)">
              <option value="">-- Chọn người dùng --</option>
              <option v-for="u in users" :key="u.id" :value="u.id">{{ u.email }} ({{ u.role }})</option>
            </select>
          </div>

          <RoleGate v-if="adminQuotaForbidden" />

          <div v-else-if="loadingUserQuota" class="state-card loading-card compact">
            <RefreshCw :size="18" class="spin" />
            <p>Đang tải quota người dùng...</p>
          </div>

          <div v-else-if="userQuotaObj" class="quota-edit-form">
            <div class="form-group checkbox-group">
              <label><input v-model="qEnabled" type="checkbox" /> Bật tính năng giới hạn Quota cho user này</label>
            </div>

            <div class="form-grid">
              <div class="form-group">
                <label>LLM Tokens / ngày:</label>
                <input v-model.number="qLlmTokens" type="number" min="0" class="text-input" />
              </div>
              <div class="form-group">
                <label>TTS Chars / ngày:</label>
                <input v-model.number="qTtsChars" type="number" min="0" class="text-input" />
              </div>
              <div class="form-group">
                <label>Tool Calls / phút:</label>
                <input v-model.number="qToolCalls" type="number" min="0" class="text-input" />
              </div>
              <div class="form-group">
                <label>RAG Bytes / tháng:</label>
                <input v-model.number="qRagBytes" type="number" min="0" class="text-input" />
              </div>
            </div>

            <button class="primary-button" type="button" :disabled="savingQuota" @click="handleSaveUserQuota">
              <span>{{ savingQuota ? 'Đang lưu...' : 'Cập nhật Quota Policy' }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Edit Setting Dialog -->
    <UiDialog :open="editSettingOpen" title="Cập nhật Cài đặt Hệ thống" @close="editSettingOpen = false">
      <form class="dialog-form" @submit.prevent="handleSaveSetting">
        <div class="form-group">
          <label>Khóa: <strong>{{ editSettingKey }}</strong></label>
        </div>
        <div class="form-group">
          <label for="set-val">Giá trị mới <span class="required">*</span></label>
          <textarea id="set-val" v-model="editSettingValue" class="text-input textarea-input font-mono" rows="4" required></textarea>
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="editSettingOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="savingSetting" @click="handleSaveSetting">
          {{ savingSetting ? 'Đang lưu...' : 'Lưu thay đổi' }}
        </button>
      </template>
    </UiDialog>
  </div>
</template>
