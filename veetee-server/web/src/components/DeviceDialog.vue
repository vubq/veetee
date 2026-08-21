<script setup lang="ts">
import { Link2Off } from '@lucide/vue'
import { onMounted, ref } from 'vue'

import { listDevices, type DeviceSummary } from '@/api/controlPlane'
import UiDialog from '@/components/UiDialog.vue'
import UiSwitch from '@/components/UiSwitch.vue'

defineProps<{ open: boolean; agentName: string }>()
defineEmits<{ close: [] }>()
const ota = ref(true)
const devices = ref<DeviceSummary[]>([])
const loading = ref(false)
const error = ref('')
onMounted(async () => {
  loading.value = true
  try {
    devices.value = await listDevices()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Không tải được thiết bị.'
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <UiDialog :open="open" :title="`${agentName} · Thiết bị`" description="Quản lý ghi chú, cập nhật OTA tự động và trạng thái liên kết." size="large" @close="$emit('close')">
    <p v-if="loading" class="empty-state">Đang tải thiết bị...</p>
    <p v-else-if="error" class="empty-state">{{ error }}</p>
    <div v-else-if="devices.length === 0" class="history-empty"><strong>Chưa có thiết bị</strong><p>Thiết bị đã liên kết sẽ xuất hiện tại đây.</p></div>
    <div v-else class="table-scroll" role="region" aria-label="Danh sách thiết bị" tabindex="0">
      <table class="data-table">
        <thead><tr><th>Thiết bị</th><th>Địa chỉ MAC</th><th>Phiên bản firmware</th><th>Thỏa thuận truy cập</th><th>Trò chuyện cuối</th><th>Cập nhật OTA</th><th>Thao tác</th></tr></thead>
        <tbody><tr v-for="device in devices" :key="device.id"><td><button class="inline-edit">{{ device.alias || device.device_id }}</button></td><td><span>{{ device.device_id }}</span></td><td>Chưa có dữ liệu</td><td><span class="neutral-badge">Veetee</span></td><td>{{ device.last_seen_at || 'Chưa có' }}</td><td><UiSwitch v-model="ota" label="Cập nhật OTA" /></td><td><button class="danger-action"><Link2Off :size="15" />Hủy liên kết</button></td></tr></tbody>
      </table>
    </div>
    <template #footer><button class="button button-outline" type="button">Liên kết thiết bị mới</button><button class="button button-ghost" type="button" @click="$emit('close')">Đóng</button></template>
  </UiDialog>
</template>
