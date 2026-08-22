<script setup lang="ts">
import UiDialog from '@/components/UiDialog.vue'

withDefaults(defineProps<{
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  busy?: boolean
}>(), { confirmLabel: 'Xác nhận', danger: false, busy: false })

defineEmits<{ close: []; confirm: [] }>()
</script>

<template>
  <UiDialog :open="open" :title="title" :description="message" :busy="busy" @close="$emit('close')">
    <template #footer>
      <button class="button button-outline" type="button" :disabled="busy" @click="$emit('close')">Hủy</button>
      <button class="button" :class="danger ? 'button-danger' : 'button-primary'" type="button" :disabled="busy" @click="$emit('confirm')">
        {{ busy ? 'Đang xử lý...' : confirmLabel }}
      </button>
    </template>
  </UiDialog>
</template>
