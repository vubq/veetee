<script setup lang="ts">
import { Link2Off, Plus } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { ApiError, listDevices, unbindDevice, type DeviceSummary } from '@/api/controlPlane'
import AddDeviceDialog from '@/components/AddDeviceDialog.vue'
import UiDialog from '@/components/UiDialog.vue'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean; agent: AgentSummary | null }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const devices = ref<DeviceSummary[]>([])
const loading = ref(false)
const error = ref('')
const addOpen = ref(false)
const confirmDevice = ref<DeviceSummary | null>(null)
const unbinding = ref(false)
const unbindError = ref('')
let loadSequence = 0

const agentDevices = computed(() => devices.value.filter((device) => device.agent_id === props.agent?.id))

function formatLastSeen(value: string | null) {
  if (!value) return 'Chưa ghi nhận'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'short' }).format(date)
}

async function loadDevices() {
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  try {
    const result = await listDevices()
    if (sequence === loadSequence) devices.value = result
  } catch (reason) {
    if (sequence === loadSequence) {
      error.value = reason instanceof ApiError ? reason.message : 'Không tải được thiết bị.'
    }
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

function requestUnbind(device: DeviceSummary) {
  confirmDevice.value = device
  unbindError.value = ''
}

async function confirmUnbind() {
  if (!confirmDevice.value || unbinding.value) return
  unbinding.value = true
  unbindError.value = ''
  try {
    await unbindDevice(confirmDevice.value.id)
    confirmDevice.value = null
    await loadDevices()
    emit('changed')
  } catch (reason) {
    unbindError.value = reason instanceof ApiError ? reason.message : 'Không thể hủy liên kết. Vui lòng thử lại.'
  } finally {
    unbinding.value = false
  }
}

async function deviceBound() {
  await loadDevices()
  emit('changed')
}

watch(
  () => [props.open, props.agent?.id] as const,
  ([open]) => {
    if (!open) loadSequence += 1
    addOpen.value = false
    confirmDevice.value = null
    unbindError.value = ''
    if (open && props.agent) void loadDevices()
  },
)
</script>

<template>
  <UiDialog :open="open" :title="`${agent?.name ?? ''} · Thiết bị`" description="Các thiết bị đang liên kết chính xác với trợ lý này." size="large" @close="emit('close')">
    <p v-if="loading" class="empty-state">Đang tải thiết bị...</p>
    <div v-else-if="error" class="history-empty"><strong>Không tải được thiết bị</strong><p>{{ error }}</p><button class="button button-outline" type="button" @click="loadDevices">Thử lại</button></div>
    <div v-else-if="agentDevices.length === 0" class="history-empty"><strong>Chưa có thiết bị</strong><p>Trợ lý này chưa được liên kết với thiết bị nào.</p><button class="button button-primary" type="button" @click="addOpen = true"><Plus :size="15" />Liên kết thiết bị</button></div>
    <div v-else class="device-list" aria-label="Danh sách thiết bị">
      <article v-for="device in agentDevices" :key="device.id" class="device-list-item">
        <div class="device-list-main">
          <div class="device-list-title"><strong>{{ device.alias || device.device_id }}</strong><span class="status-badge" :class="{ offline: !device.online }"><i></i>{{ device.online ? 'Trực tuyến' : 'Ngoại tuyến' }}</span></div>
          <dl class="device-metadata"><div><dt>Mã thiết bị</dt><dd>{{ device.device_id }}</dd></div><div><dt>Lần cuối ghi nhận</dt><dd>{{ formatLastSeen(device.last_seen_at) }}</dd></div></dl>
        </div>
        <button class="danger-action device-unbind" type="button" @click="requestUnbind(device)"><Link2Off :size="15" />Hủy liên kết</button>
      </article>
    </div>
    <template #footer><button class="button button-outline" type="button" :disabled="!agent" @click="addOpen = true"><Plus :size="15" />Liên kết thiết bị</button><button class="button button-ghost" type="button" @click="emit('close')">Đóng</button></template>
  </UiDialog>

  <AddDeviceDialog :open="addOpen" :agents="agent ? [agent] : []" :initial-agent-id="agent?.id ?? null" @close="addOpen = false" @bound="deviceBound" />

  <UiDialog :open="Boolean(confirmDevice)" title="Hủy liên kết thiết bị?" :description="`Thiết bị ${confirmDevice?.alias || confirmDevice?.device_id || ''} sẽ bị gỡ khỏi ${agent?.name ?? 'trợ lý'}.`" variant="compact" @close="confirmDevice = null">
    <p class="confirmation-copy">Bạn có thể liên kết lại thiết bị bằng mã xác minh mới.</p>
    <p v-if="unbindError" class="form-message error-message" role="alert">{{ unbindError }}</p>
    <template #footer><button class="button button-outline" type="button" :disabled="unbinding" @click="confirmDevice = null">Giữ liên kết</button><button class="button button-danger" type="button" :disabled="unbinding" @click="confirmUnbind">{{ unbinding ? 'Đang hủy...' : 'Hủy liên kết' }}</button></template>
  </UiDialog>
</template>
