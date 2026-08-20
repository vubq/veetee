<script setup lang="ts">
import { MessageSquareText } from '@lucide/vue'

import UiDialog from '@/components/UiDialog.vue'

defineProps<{ open: boolean; agentName: string }>()
defineEmits<{ close: [] }>()
</script>

<template>
  <UiDialog :open="open" :title="`${agentName} · Lịch sử trò chuyện`" description="Xem và quản lý lịch sử trò chuyện của trợ lý." size="medium" @close="$emit('close')">
    <div class="history-empty">
      <span class="empty-icon"><MessageSquareText :size="25" /></span>
      <strong>Chưa có lịch sử trò chuyện</strong>
      <p>Các cuộc trò chuyện mới sẽ xuất hiện tại đây.</p>
    </div>
    <template #footer><button class="button button-ghost" type="button" @click="$emit('close')">Đóng</button></template>
  </UiDialog>
</template>
