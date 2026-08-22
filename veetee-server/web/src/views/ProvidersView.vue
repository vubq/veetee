<script setup lang="ts">
import { Activity, AlertCircle, CheckCircle2, RefreshCw, Server, ShieldAlert } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import RoleGate from '@/components/RoleGate.vue'
import { ApiError, checkProviderHealth, listProviders, type ProviderCatalogItem, updateProvider } from '@/api/controlPlane'

const providers = ref<ProviderCatalogItem[]>([])
const loading = ref(true)
const error = ref('')
const actionError = ref('')
const isForbidden = ref(false)
const updatingId = ref<string | null>(null)

async function loadProviders() {
  loading.value = true
  error.value = ''
  actionError.value = ''
  isForbidden.value = false
  try {
    providers.value = await listProviders()
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      isForbidden.value = true
    } else {
      error.value = err instanceof Error ? err.message : 'Không thể tải danh sách nhà cung cấp.'
    }
  } finally {
    loading.value = false
  }
}

async function handleToggleEnabled(item: ProviderCatalogItem) {
  updatingId.value = `${item.kind}:${item.provider_id}`
  actionError.value = ''
  try {
    const updated = await updateProvider(item.kind, item.provider_id, {
      expected_version: item.config_version ?? 1,
      enabled: !item.enabled,
    })
    const idx = providers.value.findIndex(p => p.kind === item.kind && p.provider_id === item.provider_id)
    if (idx >= 0) providers.value[idx] = updated
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để thay đổi trạng thái nhà cung cấp.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Xung đột phiên bản: Cấu hình nhà cung cấp đã thay đổi. Vui lòng tải lại.'
      await loadProviders()
    } else {
      actionError.value = err instanceof Error ? err.message : 'Không thể cập nhật nhà cung cấp.'
    }
  } finally {
    updatingId.value = null
  }
}

async function handleSetDefault(item: ProviderCatalogItem) {
  updatingId.value = `${item.kind}:${item.provider_id}`
  actionError.value = ''
  try {
    const updated = await updateProvider(item.kind, item.provider_id, {
      expected_version: item.config_version ?? 1,
      is_default: true,
    })
    const idx = providers.value.findIndex(p => p.kind === item.kind && p.provider_id === item.provider_id)
    if (idx >= 0) providers.value[idx] = updated
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để thiết lập nhà cung cấp mặc định.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Xung đột phiên bản. Vui lòng tải lại dữ liệu.'
      await loadProviders()
    } else {
      actionError.value = err instanceof Error ? err.message : 'Không thể cập nhật nhà cung cấp mặc định.'
    }
  } finally {
    updatingId.value = null
  }
}

async function handleHealthCheck(item: ProviderCatalogItem) {
  updatingId.value = `${item.kind}:${item.provider_id}`
  actionError.value = ''
  try {
    await checkProviderHealth(item.kind, item.provider_id)
    await loadProviders()
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để thực hiện kiểm tra sức khỏe nhà cung cấp.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Kiểm tra sức khỏe thất bại.'
    }
  } finally {
    updatingId.value = null
  }
}

onMounted(() => loadProviders())
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Nhà cung cấp</h1>
        <p class="subtitle">Quản lý catalog nhà cung cấp dịch vụ ASR, LLM, TTS và trạng thái hoạt động.</p>
      </div>
      <button class="icon-button" type="button" title="Tải lại" :disabled="loading" @click="loadProviders">
        <RefreshCw :size="16" :class="{ 'spin': loading }" />
      </button>
    </div>

    <RoleGate v-if="isForbidden" />

    <div v-else-if="loading" class="state-card loading-card">
      <RefreshCw :size="24" class="spin" />
      <p>Đang tải danh sách nhà cung cấp...</p>
    </div>

    <div v-else-if="error" class="state-card error-card">
      <AlertCircle :size="24" />
      <p>{{ error }}</p>
      <button class="primary-button" type="button" @click="loadProviders">Thử lại</button>
    </div>

    <div v-else class="view-content">
      <div v-if="actionError" class="alert-box is-error" role="alert">
        <ShieldAlert :size="16" />
        <span>{{ actionError }}</span>
      </div>

      <div class="provider-grid">
        <div v-for="item in providers" :key="`${item.kind}:${item.provider_id}`" class="provider-card">
          <div class="provider-header">
            <div class="provider-title">
              <Server :size="20" class="provider-icon" />
              <div>
                <h3>{{ item.provider_id }}</h3>
                <span class="badge kind-badge">{{ item.kind.toUpperCase() }}</span>
              </div>
            </div>
            <div class="provider-badges">
              <span v-if="item.is_default || item.default" class="badge default-badge">Mặc định</span>
              <span class="badge" :class="item.enabled ? 'enabled-badge' : 'disabled-badge'">
                {{ item.enabled ? 'Đã bật' : 'Đã tắt' }}
              </span>
            </div>
          </div>

          <div class="provider-body">
            <div class="health-status">
              <span class="label">Sức khỏe:</span>
              <span
                class="health-pill"
                :class="{
                  'is-ok': item.health?.status === 'ok',
                  'is-degraded': item.health?.status === 'degraded',
                  'is-unknown': !item.health || item.health?.status === 'unknown',
                }"
              >
                <CheckCircle2 v-if="item.health?.status === 'ok'" :size="14" />
                <AlertCircle v-else :size="14" />
                <span>{{ item.health?.details || item.health?.status || 'Chưa kiểm tra' }}</span>
              </span>
            </div>

            <div class="models-list">
              <span class="label">Mô hình hỗ trợ:</span>
              <div class="model-tags">
                <span v-for="m in item.models" :key="m" class="model-tag">{{ m }}</span>
              </div>
            </div>
          </div>

          <div class="provider-footer">
            <button
              class="secondary-button compact"
              type="button"
              :disabled="updatingId === `${item.kind}:${item.provider_id}`"
              @click="handleToggleEnabled(item)"
            >
              {{ item.enabled ? 'Tắt nhà cung cấp' : 'Bật nhà cung cấp' }}
            </button>

            <button
              v-if="!item.is_default && !item.default"
              class="secondary-button compact"
              type="button"
              :disabled="updatingId === `${item.kind}:${item.provider_id}`"
              @click="handleSetDefault(item)"
            >
              Đặt làm mặc định
            </button>

            <button
              class="icon-button compact"
              type="button"
              title="Kiểm tra sức khỏe"
              :disabled="updatingId === `${item.kind}:${item.provider_id}`"
              @click="handleHealthCheck(item)"
            >
              <Activity :size="16" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
