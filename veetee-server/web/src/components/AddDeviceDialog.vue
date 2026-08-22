<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch } from 'vue'

import { bindDevice, type DeviceSummary } from '@/api/controlPlane'
import UiDialog from '@/components/UiDialog.vue'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean; agents: AgentSummary[]; defaultAgentId?: string }>()
const emit = defineEmits<{ close: []; bound: [device: DeviceSummary] }>()
const deviceId = ref('')
const code = ref('')
const alias = ref('')
const agentId = ref('')
const busy = ref(false)
const error = ref('')
const status = ref('')
const idempotencyKey = ref('')
const codeInput = useTemplateRef<HTMLInputElement>('codeInput')
const valid = computed(() => Boolean(deviceId.value.trim()) && /^\d{6}$/.test(code.value))
const digits = computed(() => Array.from({ length: 6 }, (_, index) => code.value[index] ?? ''))
const activeSlot = computed(() => Math.min(code.value.length, 5))

function updateCode(event: Event) {
  code.value = (event.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6)
}

async function submit() {
  if (!valid.value || busy.value) return
  busy.value = true
  error.value = ''
  status.value = 'Đang xác minh mã và liên kết thiết bị...'
  try {
    const device = await bindDevice({
      device_id: deviceId.value.trim(), code: code.value, alias: alias.value.trim(),
      agent_id: agentId.value || null,
    }, idempotencyKey.value)
    status.value = 'Đã liên kết thiết bị.'
    emit('bound', device)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Không liên kết được thiết bị.'
    status.value = ''
  } finally {
    busy.value = false
  }
}

watch(() => props.open, async (open) => {
  if (!open) return
  deviceId.value = ''; code.value = ''; alias.value = ''; error.value = ''; status.value = ''
  agentId.value = props.defaultAgentId || props.agents[0]?.id || ''
  idempotencyKey.value = crypto.randomUUID()
  await nextTick()
  codeInput.value?.focus()
})
</script>

<template>
  <UiDialog :open="open" title="Thêm thiết bị" description="Nhập mã 6 chữ số đang hiển thị trên thiết bị." variant="compact" :busy="busy" @close="emit('close')">
    <form class="compact-form" @submit.prevent="submit">
      <label><span>Mã thiết bị</span><input v-model="deviceId" class="text-input" required maxlength="128" autocomplete="off" placeholder="Device-Id" /></label>
      <label><span>Tên gợi nhớ</span><input v-model="alias" class="text-input" maxlength="128" placeholder="Ví dụ: Phòng khách" /></label>
      <label><span>Trợ lý</span><select v-model="agentId" class="text-input"><option value="">Chưa gán trợ lý</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option></select></label>
      <div class="device-code-field">
        <div class="device-code-label">Mã xác minh thiết bị</div>
        <div class="device-code-input">
          <input ref="codeInput" :value="code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="6" aria-label="Mã xác minh" @input="updateCode" />
          <button type="button" class="device-code-slots" aria-hidden="true" tabindex="-1" @click="codeInput?.focus()"><span v-for="(digit, index) in digits" :key="index" :class="{ active: index === activeSlot && code.length < 6 }">{{ digit }}</span></button>
        </div>
      </div>
      <p v-if="status" class="form-status success">{{ status }}</p>
      <p v-if="error" class="form-status error">{{ error }}</p>
    </form>
    <template #footer>
      <button class="button button-outline" type="button" :disabled="busy" @click="emit('close')">Hủy</button>
      <button class="button button-primary" type="button" :disabled="!valid || busy" @click="submit">{{ busy ? 'Đang thêm...' : 'Thêm' }}</button>
    </template>
  </UiDialog>
</template>
