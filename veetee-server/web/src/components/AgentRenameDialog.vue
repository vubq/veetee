<script setup lang="ts">
import { ref, watch } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import { updateAgent, type ApiError } from '@/api/controlPlane'
import type { AgentSummary } from '@/types/agent'

const props = defineProps<{ open: boolean; agent: AgentSummary | null }>()
const emit = defineEmits<{ close: []; renamed: [agent: AgentSummary] }>()

const name = ref('')
const saving = ref(false)
const error = ref('')

watch(
  () => [props.open, props.agent] as const,
  ([open, agent]) => {
    if (!open) return
    name.value = agent?.name ?? ''
    saving.value = false
    error.value = ''
  },
)

async function submit() {
  const target = props.agent
  const newName = name.value.trim()
  if (!target || !newName || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await updateAgent({ ...target, name: newName })
    emit('renamed', updated)
    emit('close')
  } catch (reason) {
    if ((reason as ApiError).status === 409) {
      error.value = 'Trợ lý vừa được thay đổi ở nơi khác. Hãy đóng và thử lại.'
    } else {
      error.value = reason instanceof Error ? reason.message : 'Không đổi được tên trợ lý.'
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <UiDialog
    :open="open"
    title="Đổi tên trợ lý"
    description="Tên mới được áp dụng ngay cho hồ sơ trợ lý."
    variant="compact"
    @close="emit('close')"
  >
    <form class="config-form" @submit.prevent="submit">
      <label class="config-field">
        <span>Tên trợ lý</span>
        <input v-model="name" class="text-input" required maxlength="120" autocomplete="off" data-testid="rename-input" />
      </label>
      <p v-if="error" class="field-help" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <button class="button button-outline" type="button" :disabled="saving" @click="emit('close')">Hủy</button>
      <button class="button button-primary" type="button" :disabled="saving || !name.trim()" data-testid="rename-save" @click="submit">{{ saving ? 'Đang lưu...' : 'Lưu tên mới' }}</button>
    </template>
  </UiDialog>
</template>
