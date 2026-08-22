<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch } from 'vue'

import { ApiError, bindDevice, type DeviceSummary } from '@/api/controlPlane'
import UiDialog from '@/components/UiDialog.vue'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean; agents: AgentSummary[]; initialAgentId: string | null }>()
const emit = defineEmits<{ close: []; bound: [device: DeviceSummary] }>()
const code = ref('')
const agentId = ref('')
const busy = ref(false)
const error = ref('')
const success = ref('')
const valid = computed(() => Boolean(agentId.value) && /^\d{6}$/.test(code.value))
const codeInput = useTemplateRef<HTMLInputElement>('codeInput')
const agentSelect = useTemplateRef<HTMLSelectElement>('agentSelect')
const digits = computed(() => Array.from({ length: 6 }, (_, index) => code.value[index] ?? ''))
const activeSlot = computed(() => Math.min(code.value.length, 5))

function updateCode(event: Event) {
  code.value = (event.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6)
  error.value = ''
}

async function submit() {
  if (!valid.value || busy.value) return
  busy.value = true
  error.value = ''
  success.value = ''
  try {
    const device = await bindDevice(agentId.value, code.value)
    success.value = `Đã liên kết thiết bị với ${props.agents.find((agent) => agent.id === agentId.value)?.name ?? 'trợ lý'}.`
    emit('bound', device)
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : 'Không thể liên kết thiết bị. Vui lòng thử lại.'
  } finally {
    busy.value = false
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    code.value = ''
    agentId.value = props.initialAgentId && props.agents.some((agent) => agent.id === props.initialAgentId)
      ? props.initialAgentId
      : props.agents.length === 1 ? props.agents[0]?.id ?? '' : ''
    busy.value = false
    error.value = ''
    success.value = ''
    await nextTick()
    if (agentId.value) codeInput.value?.focus()
    else agentSelect.value?.focus()
  },
)
</script>

<template>
  <UiDialog :open="open" title="Thêm thiết bị" description="Thiết bị chưa liên kết sẽ đọc mã xác minh 6 chữ số sau khi khởi động hoặc khởi động lại." variant="compact" @close="emit('close')">
    <form class="bind-device-form" @submit.prevent="submit">
      <label v-if="agents.length > 1" class="bind-agent-field">
        <span>Liên kết với trợ lý</span>
        <select ref="agentSelect" v-model="agentId" class="text-input" name="device-agent" data-dialog-autofocus :disabled="busy" @change="error = ''; nextTick(() => codeInput?.focus())">
          <option value="" disabled>Chọn một trợ lý</option>
          <option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option>
        </select>
      </label>
      <div v-else-if="agents.length === 1" class="selected-agent-note"><span>Trợ lý</span><strong>{{ agents[0]?.name }}</strong></div>
      <p v-else class="form-message error-message">Chưa có trợ lý để liên kết. Hãy tạo trợ lý trước.</p>
      <div class="device-code-field">
      <div class="device-code-label">Mã xác minh thiết bị</div>
      <div class="device-code-input">
        <input ref="codeInput" :value="code" name="device-verification-code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="6" aria-label="Mã xác minh" :data-dialog-autofocus="agents.length === 1 ? '' : undefined" :disabled="busy || !agentId" @input="updateCode" />
        <button type="button" class="device-code-slots" aria-hidden="true" tabindex="-1" @click="codeInput?.focus()">
          <span v-for="(digit, index) in digits" :key="index" :class="{ active: index === activeSlot && code.length < 6 }">{{ digit }}</span>
        </button>
      </div>
      </div>
      <p v-if="error" class="form-message error-message" role="alert">{{ error }}</p>
      <p v-if="success" class="form-message success-message" role="status">{{ success }}</p>
      <button class="visually-hidden" type="submit" tabindex="-1" aria-hidden="true">Thêm</button>
    </form>
    <template #footer>
      <button class="button button-outline" type="button" :disabled="busy" @click="emit('close')">{{ success ? 'Đóng' : 'Hủy' }}</button>
      <button v-if="!success" class="button button-primary" type="button" :disabled="!valid || busy" data-testid="bind-submit" @click="submit">{{ busy ? 'Đang thêm...' : 'Thêm' }}</button>
    </template>
  </UiDialog>
</template>
