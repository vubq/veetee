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
  <article class="fleet-row" :class="{ 'fleet-row--revoked': device.revoked }">
    <div class="fleet-row__identity">
      <div class="fleet-row__avatar"><Cpu :size="17" /></div>
      <div>
        <div class="fleet-row__name">
          <strong>{{ device.name || device.device_id }}</strong>
          <UiBadge :tone="statusTone()" dot>{{ statusLabel() }}</UiBadge>
        </div>
        <span>{{ device.device_id }}</span>
        <small>{{ device.client_id }}</small>
      </div>
    </div>

    <div class="fleet-row__seen">
      <span>LAST SEEN</span>
      <strong>{{ device.last_seen_label || 'Chưa có dữ liệu' }}</strong>
    </div>

    <div class="fleet-row__controls">
      <UiInput
        :model-value="device.name"
        label="Device name"
        placeholder="Tên thiết bị"
        :disabled="device.revoked"
        @change="emit('update', device, { name: $event.target.value })"
      />
      <UiSelect
        :model-value="device.assistant_id"
        label="Assistant binding"
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

    <div class="fleet-row__action">
      <UiButton v-if="!device.revoked" variant="danger-ghost" size="sm" @click="emit('revoke', device)">
        <template #icon><ShieldOff :size="13" /></template>
        Revoke
      </UiButton>
      <span v-else class="fleet-row__revoked"><CircleOff :size="13" /> Credential disabled</span>
    </div>
  </article>
</template>