<script setup lang="ts">
import { computed, nextTick, ref, useTemplateRef, watch } from 'vue'

import UiDialog from '@/components/UiDialog.vue'

const props = defineProps<{ open: boolean }>()
defineEmits<{ close: [] }>()
const code = ref('')
const valid = computed(() => /^\d{6}$/.test(code.value))
const codeInput = useTemplateRef<HTMLInputElement>('codeInput')
const digits = computed(() => Array.from({ length: 6 }, (_, index) => code.value[index] ?? ''))
const activeSlot = computed(() => Math.min(code.value.length, 5))

function updateCode(event: Event) {
  code.value = (event.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6)
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    code.value = ''
    await nextTick()
    codeInput.value?.focus()
  },
)
</script>

<template>
  <UiDialog :open="open" title="Thêm thiết bị" description="Thiết bị chưa liên kết sẽ đọc mã xác minh 6 chữ số sau khi khởi động hoặc khởi động lại." variant="compact" @close="$emit('close')">
    <div class="device-code-field">
      <div class="device-code-label">Mã xác minh thiết bị</div>
      <div class="device-code-input">
        <input ref="codeInput" :value="code" name="device-verification-code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="6" aria-label="Mã xác minh" @input="updateCode" />
        <button type="button" class="device-code-slots" aria-hidden="true" tabindex="-1" @click="codeInput?.focus()">
          <span v-for="(digit, index) in digits" :key="index" :class="{ active: index === activeSlot && code.length < 6 }">{{ digit }}</span>
        </button>
      </div>
    </div>
    <template #footer>
      <button class="button button-outline" type="button" @click="$emit('close')">Hủy</button>
      <button class="button button-primary" type="button" :disabled="!valid">Thêm</button>
    </template>
  </UiDialog>
</template>
