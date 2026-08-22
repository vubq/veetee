<script setup lang="ts">
import { ref, watch } from 'vue'

import { createAgent } from '@/api/controlPlane'
import UiDialog from '@/components/UiDialog.vue'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; created: [agent: AgentSummary] }>()
const name = ref('')
const rolePrompt = ref('')
const saving = ref(false)
const error = ref('')

watch(() => props.open, (open) => {
  if (!open) return
  name.value = ''
  rolePrompt.value = ''
  error.value = ''
})

async function submit() {
  saving.value = true
  error.value = ''
  try {
    emit('created', await createAgent(name.value.trim(), rolePrompt.value.trim()))
    emit('close')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Không tạo được trợ lý.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UiDialog :open="open" title="Tạo trợ lý mới" description="Tạo hồ sơ trợ lý và tùy chỉnh vai trò chi tiết sau khi lưu." variant="compact" @close="emit('close')">
    <form class="config-form" @submit.prevent="submit">
      <label class="config-field"><span>Tên trợ lý</span><input v-model="name" class="text-input" required maxlength="120" autocomplete="off" /></label>
      <label class="config-field"><span>Vai trò tổng quát</span><textarea v-model="rolePrompt" class="config-textarea" maxlength="12000" /></label>
      <p v-if="error" class="field-help">{{ error }}</p>
    </form>
    <template #footer><button class="button button-outline" type="button" @click="emit('close')">Hủy</button><button class="button button-primary" type="button" :disabled="saving || !name.trim()" data-testid="create-agent-submit" @click="submit">{{ saving ? 'Đang tạo...' : 'Tạo trợ lý' }}</button></template>
  </UiDialog>
</template>
