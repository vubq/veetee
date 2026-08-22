<script setup lang="ts">
import { KeyRound, Link2Off } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { listDevices, patchDevice, recoverDevice, unbindDevice, type DeviceSummary } from '@/api/controlPlane'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import UiDialog from '@/components/UiDialog.vue'
import UiSwitch from '@/components/UiSwitch.vue'

const props = defineProps<{ open: boolean; agentId: string; agentName: string }>()
const emit = defineEmits<{ close: []; 'request-bind': [] }>()
const devices = ref<DeviceSummary[]>([])
const loading = ref(false)
const error = ref('')
const savingIds = ref(new Set<string>())
const confirmDevice = ref<DeviceSummary | null>(null)
const confirmKey = ref('')
const unbinding = ref(false)
const recoveryDevice = ref<DeviceSummary | null>(null)
const recoveryClientId = ref('')
const recoveryToken = ref('')
const recovering = ref(false)

const filteredDevices = computed(() => devices.value.filter((device) => device.agent_id === props.agentId))

async function loadDevices() {
  loading.value = true; error.value = ''
  try { devices.value = await listDevices() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : 'Không tải được thiết bị.' }
  finally { loading.value = false }
}

async function toggleAutoUpdate(device: DeviceSummary, value: boolean) {
  const previous = device.auto_update
  device.auto_update = value
  savingIds.value = new Set(savingIds.value).add(device.id)
  try { Object.assign(device, await patchDevice(device.device_id, { auto_update: value })) }
  catch (reason) {
    device.auto_update = previous
    error.value = reason instanceof Error ? reason.message : 'Không cập nhật được thiết bị.'
  } finally {
    const next = new Set(savingIds.value); next.delete(device.id); savingIds.value = next
  }
}

function askUnbind(device: DeviceSummary) {
  confirmDevice.value = device
  confirmKey.value = crypto.randomUUID()
}

async function confirmUnbind() {
  if (!confirmDevice.value) return
  unbinding.value = true; error.value = ''
  try {
    await unbindDevice(confirmDevice.value.device_id, confirmKey.value)
    devices.value = devices.value.filter((device) => device.id !== confirmDevice.value?.id)
    confirmDevice.value = null
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'Không hủy được liên kết.' }
  finally { unbinding.value = false }
}

function startRecovery(device: DeviceSummary) {
  recoveryDevice.value = device; recoveryClientId.value = ''; recoveryToken.value = ''; error.value = ''
}

function clearRecovery() {
  if (recovering.value) return
  recoveryDevice.value = null; recoveryClientId.value = ''; recoveryToken.value = ''
}

async function recover() {
  if (!recoveryDevice.value || !recoveryClientId.value.trim()) return
  recovering.value = true; error.value = ''
  try {
    const result = await recoverDevice(recoveryDevice.value.device_id, recoveryClientId.value.trim())
    Object.assign(recoveryDevice.value, result.device)
    recoveryToken.value = result.recovery_token
  } catch (reason) { error.value = reason instanceof Error ? reason.message : 'Không khôi phục được thiết bị.' }
  finally { recovering.value = false }
}

watch(() => props.open, (open) => {
  if (open) void loadDevices()
  else clearRecovery()
})
</script>

<template>
  <UiDialog :open="open" :title="`${agentName} · Thiết bị`" description="Quản lý trạng thái liên kết và chính sách OTA của trợ lý." size="large" @close="emit('close')">
    <p v-if="loading" class="empty-state">Đang tải thiết bị...</p>
    <p v-else-if="error" class="inline-alert error">{{ error }}</p>
    <div v-if="!loading && filteredDevices.length === 0" class="history-empty"><strong>Chưa có thiết bị</strong><p>Liên kết một thiết bị với trợ lý này để bắt đầu.</p></div>
    <div v-else-if="!loading" class="device-card-list">
      <article v-for="device in filteredDevices" :key="device.id" class="device-card">
        <header><div><strong>{{ device.alias || device.device_id }}</strong><small>{{ device.device_id }}</small></div><span class="status-badge" :class="{ offline: !device.online }"><i></i>{{ device.online ? 'Trực tuyến' : 'Ngoại tuyến' }}</span></header>
        <dl class="detail-grid">
          <div><dt>Liên kết</dt><dd>{{ device.status }}</dd></div>
          <div><dt>Client ID</dt><dd>{{ device.client_id || 'Chưa có' }}</dd></div>
          <div><dt>Board</dt><dd>{{ device.board || 'Chưa ghi nhận' }}</dd></div>
          <div><dt>Chip / partition</dt><dd>{{ [device.chip, device.partition].filter(Boolean).join(' · ') || 'Chưa ghi nhận' }}</dd></div>
          <div><dt>Firmware</dt><dd>{{ device.current_firmware_version || 'Chưa ghi nhận' }}</dd></div>
          <div><dt>Kênh / cohort</dt><dd>{{ device.channel || 'stable' }} · {{ device.cohort || 'Chưa có' }}</dd></div>
          <div><dt>Lần thấy cuối</dt><dd>API hiện chưa cung cấp</dd></div>
        </dl>
        <footer>
          <label class="switch-label"><UiSwitch :model-value="device.auto_update" label="Cập nhật OTA tự động" :disabled="savingIds.has(device.id)" @update:model-value="toggleAutoUpdate(device, $event)" />OTA tự động</label>
          <button v-if="device.status === 'recovery_required'" class="button button-outline" type="button" @click="startRecovery(device)"><KeyRound :size="15" />Khôi phục</button>
          <button class="danger-action" type="button" @click="askUnbind(device)"><Link2Off :size="15" />Hủy liên kết</button>
        </footer>
      </article>
    </div>
    <template #footer><button class="button button-primary" type="button" @click="emit('request-bind')">Liên kết thiết bị</button><button class="button button-ghost" type="button" @click="emit('close')">Đóng</button></template>
  </UiDialog>

  <ConfirmDialog :open="Boolean(confirmDevice)" title="Hủy liên kết thiết bị?" :message="`Thiết bị ${confirmDevice?.alias || confirmDevice?.device_id || ''} sẽ mất quyền truy cập hiện tại.`" confirm-label="Hủy liên kết" danger :busy="unbinding" @close="confirmDevice = null" @confirm="confirmUnbind" />

  <UiDialog :open="Boolean(recoveryDevice)" title="Khôi phục Client ID" description="Chỉ dùng khi thiết bị ở trạng thái recovery_required." :busy="recovering" @close="clearRecovery">
    <div v-if="!recoveryToken" class="compact-form">
      <label><span>Client ID mới từ thiết bị</span><input v-model="recoveryClientId" class="text-input" maxlength="128" autocomplete="off" /></label>
      <p v-if="error" class="form-status error">{{ error }}</p>
    </div>
    <div v-else class="recovery-secret">
      <strong>Token khôi phục chỉ hiển thị trong lần này</strong>
      <code>{{ recoveryToken }}</code>
      <p>Chuyển token này trực tiếp vào luồng discovery của đúng thiết bị. Không lưu vào trình duyệt, log hay dùng làm credential WebSocket. Đóng hộp thoại sẽ xóa token khỏi giao diện.</p>
    </div>
    <template #footer><button class="button button-outline" type="button" :disabled="recovering" @click="clearRecovery">Đóng và xóa</button><button v-if="!recoveryToken" class="button button-primary" type="button" :disabled="recovering || !recoveryClientId.trim()" @click="recover">{{ recovering ? 'Đang khôi phục...' : 'Tạo token một lần' }}</button></template>
  </UiDialog>
</template>
