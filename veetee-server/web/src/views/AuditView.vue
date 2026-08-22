<script setup lang="ts">
import { AlertCircle, ClipboardList, RefreshCw, Search } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { ApiError, searchAuditLogs, type AuditLogItem } from '@/api/controlPlane'
import RoleGate from '@/components/RoleGate.vue'

const items = ref<AuditLogItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const error = ref('')
const forbidden = ref(false)
const action = ref('')
const resourceType = ref('')
const actorUserId = ref('')
const startTime = ref('')
const endTime = ref('')

function toApiTime(value: string): string | undefined {
  return value ? new Date(value).toISOString() : undefined
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value))
}

async function loadAuditLogs() {
  loading.value = true
  error.value = ''
  forbidden.value = false
  try {
    const result = await searchAuditLogs({
      page: page.value,
      limit: 50,
      action: action.value.trim() || undefined,
      resource_type: resourceType.value.trim() || undefined,
      actor_user_id: actorUserId.value.trim() || undefined,
      start_time: toApiTime(startTime.value),
      end_time: toApiTime(endTime.value),
    })
    items.value = result.items
    total.value = result.total
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 403) forbidden.value = true
    else error.value = reason instanceof Error ? reason.message : 'Không thể tải nhật ký audit.'
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  void loadAuditLogs()
}

onMounted(loadAuditLogs)
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Nhật ký audit</h1>
        <p class="subtitle">Tra cứu các thay đổi quản trị theo tác nhân, hành động, tài nguyên và thời gian.</p>
      </div>
      <button class="icon-button" type="button" title="Tải lại" :disabled="loading" @click="loadAuditLogs">
        <RefreshCw :size="16" :class="{ spin: loading }" />
      </button>
    </div>

    <RoleGate v-if="forbidden" />
    <template v-else>
      <form class="card audit-filter-card" @submit.prevent="applyFilters">
        <div class="form-grid audit-filter-grid">
          <label class="form-group">Hành động<input v-model="action" class="text-input" placeholder="user.updated" /></label>
          <label class="form-group">Loại tài nguyên<input v-model="resourceType" class="text-input" placeholder="user" /></label>
          <label class="form-group">Actor user ID<input v-model="actorUserId" class="text-input" placeholder="UUID" /></label>
          <label class="form-group">Từ thời điểm<input v-model="startTime" class="text-input" type="datetime-local" /></label>
          <label class="form-group">Đến thời điểm<input v-model="endTime" class="text-input" type="datetime-local" /></label>
        </div>
        <button class="primary-button" type="submit" :disabled="loading"><Search :size="16" /> Tra cứu</button>
      </form>

      <div v-if="error" class="alert-box is-error" role="alert"><AlertCircle :size="16" />{{ error }}</div>
      <div v-else-if="loading" class="state-card loading-card"><RefreshCw :size="20" class="spin" /><p>Đang tải nhật ký...</p></div>
      <div v-else-if="items.length === 0" class="state-card"><ClipboardList :size="28" /><p>Không có sự kiện phù hợp.</p></div>
      <div v-else class="card table-container">
        <table class="data-table">
          <thead><tr><th>Thời gian</th><th>Hành động</th><th>Tài nguyên</th><th>Tác nhân</th><th>Metadata đã lọc</th></tr></thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td class="text-sm">{{ formatTime(item.created_at) }}</td>
              <td><span class="badge kind-badge">{{ item.action }}</span></td>
              <td><strong>{{ item.resource_type }}</strong><br /><span class="text-sm text-muted font-mono">{{ item.resource_id }}</span></td>
              <td class="font-mono text-sm">{{ item.actor_user_id || 'system' }}</td>
              <td><code class="audit-metadata">{{ JSON.stringify(item.metadata) }}</code></td>
            </tr>
          </tbody>
        </table>
        <p class="table-summary">Hiển thị {{ items.length }} / {{ total }} sự kiện.</p>
      </div>
    </template>
  </div>
</template>
