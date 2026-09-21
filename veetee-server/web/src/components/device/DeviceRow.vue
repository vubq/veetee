<script setup>
import { CircleOff, Cpu, ShieldOff } from '@lucide/vue'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'
import UiInput from '../ui/UiInput.vue'
import UiSelect from '../ui/UiSelect.vue'

const props = defineProps({
  device: { type: Object, required: true },
  assistants: { type: Array, default: () => [] },
})
const emit = defineEmits(['update', 'revoke'])

const options = () => props.assistants.map(item => ({ value: item.id, label: item.name }))
function statusTone() {
  if (props.device.revoked) return 'danger'
  if (props.device.online) return 'success'
  return 'neutral'
}
function statusLabel() {
  if (props.device.revoked) return 'Revoked'
  return props.device.online ? 'Online' : 'Offline'
}
</script>

<template>
  <article class="device-item" :class="{ 'device-item--revoked': device.revoked }">
    <div class="device-item__header">
      <div class="device-item__identity">
        <span class="device-item__icon"><Cpu :size="18" /></span>
        <div>
          <div class="device-item__title">
            <strong>{{ device.name || device.device_id }}</strong>
            <UiBadge :tone="statusTone()" dot>{{ statusLabel() }}</UiBadge>
          </div>
          <p>{{ device.device_id }} · {{ device.client_id }}</p>
        </div>
      </div>

      <div class="device-item__meta">
        <span>Last seen</span>
        <strong>{{ device.last_seen_label || 'Chưa có dữ liệu' }}</strong>
      </div>
    </div>

    <div class="device-item__controls">
      <UiInput
        :model-value="device.name"
        label="Device name"
        placeholder="Tên thiết bị"
        :disabled="device.revoked"
        @change="emit('update', device, { name: $event.target.value })"
      />
      <UiSelect
        :model-value="device.assistant_id"
        label="Assistant"
        :options="options()"
        placeholder="Chọn Assistant"
        :disabled="device.revoked"
        @change="emit('update', device, { assistant_id: $event })"
      />
      <UiInput
        :model-value="device.owner_id || ''"
        label="Memory owner"
        placeholder="Owner mặc định"
        maxlength="128"
        :disabled="device.revoked"
        @change="emit('update', device, { owner_id: $event.target.value })"
      />
    </div>

    <div class="device-item__footer">
      <span v-if="device.revoked" class="device-item__revoked"><CircleOff :size="14" /> Credential disabled</span>
      <span v-else class="device-item__hint">Thay đổi field sẽ được lưu khi rời input/chọn giá trị.</span>
      <UiButton v-if="!device.revoked" variant="danger-ghost" size="sm" @click="emit('revoke', device)">
        <template #icon><ShieldOff :size="14" /></template>
        Revoke
      </UiButton>
    </div>
  </article>
</template>
