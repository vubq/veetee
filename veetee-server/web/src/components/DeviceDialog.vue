<script setup lang="ts">
import { Copy, Link2Off } from '@lucide/vue'
import { ref } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import UiSwitch from '@/components/UiSwitch.vue'

defineProps<{ open: boolean; agentName: string }>()
defineEmits<{ close: [] }>()
const ota = ref(true)
</script>

<template>
  <UiDialog :open="open" :title="`${agentName} · Thiết bị`" description="Quản lý ghi chú, cập nhật OTA tự động và trạng thái liên kết." size="large" @close="$emit('close')">
    <div class="table-scroll" role="region" aria-label="Danh sách thiết bị" tabindex="0">
      <table class="data-table">
        <thead><tr><th>Thiết bị</th><th>Địa chỉ MAC</th><th>Phiên bản firmware</th><th>Thỏa thuận truy cập</th><th>Trò chuyện cuối</th><th>Cập nhật OTA</th><th>Thao tác</th></tr></thead>
        <tbody><tr><td><button class="inline-edit">veetee-esp32-s3</button></td><td><button class="copy-button" title="Sao chép"><span>28:84:85:••:••:1C</span><Copy :size="14" /></button></td><td>2.4.2</td><td><span class="neutral-badge">Mã nguồn mở</span></td><td>15 giờ trước</td><td><UiSwitch v-model="ota" label="Cập nhật OTA" /></td><td><button class="danger-action"><Link2Off :size="15" />Hủy liên kết</button></td></tr></tbody>
      </table>
    </div>
    <template #footer><button class="button button-outline" type="button">Liên kết thiết bị mới</button><button class="button button-ghost" type="button" @click="$emit('close')">Đóng</button></template>
  </UiDialog>
</template>
