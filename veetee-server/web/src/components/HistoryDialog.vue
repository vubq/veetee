<script setup lang="ts">
import { MessageSquareText } from '@lucide/vue'
import { ref, watch } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import { listConversations, type ConversationSummary } from '@/api/controlPlane'

const props = defineProps<{ open: boolean; agentName: string; agentId?: string }>()
defineEmits<{ close: [] }>()
const conversations = ref<ConversationSummary[]>([])
const error = ref('')
async function loadConversations() {
  error.value = ''
  try {
    conversations.value = await listConversations(props.agentId)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'Không tải được lịch sử.'
  }
}
watch(() => props.open, (open) => {
  if (open) void loadConversations()
})
</script>

<template>
  <UiDialog :open="open" :title="`${agentName} · Lịch sử trò chuyện`" description="Xem và quản lý lịch sử trò chuyện của trợ lý." size="medium" @close="$emit('close')">
    <p v-if="error" class="history-empty">{{ error }}</p>
    <div v-else-if="conversations.length === 0" class="history-empty">
      <span class="empty-icon"><MessageSquareText :size="25" /></span>
      <strong>Chưa có lịch sử trò chuyện</strong>
      <p>Các cuộc trò chuyện mới sẽ xuất hiện tại đây.</p>
    </div>
    <div v-else class="history-list"><article v-for="conversation in conversations" :key="conversation.id"><strong>{{ conversation.title || 'Cuộc trò chuyện' }}</strong><p>{{ conversation.summary || 'Chưa có tóm tắt' }} · {{ conversation.turn_count }} lượt</p></article></div>
    <template #footer><button class="button button-ghost" type="button" @click="$emit('close')">Đóng</button></template>
  </UiDialog>
</template>
