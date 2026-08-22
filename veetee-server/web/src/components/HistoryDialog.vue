<script setup lang="ts">
import { MessageSquareText } from '@lucide/vue'
import { ref, watch } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import { listConversations, type ConversationSummary } from '@/api/controlPlane'

const props = defineProps<{ open: boolean; agentName: string; agentId?: string }>()
defineEmits<{ close: [] }>()

const conversations = ref<ConversationSummary[]>([])
const loading = ref(false)
const error = ref('')
let loadSequence = 0

async function loadConversations() {
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  try {
    const result = await listConversations(props.agentId)
    if (sequence === loadSequence) conversations.value = result
  } catch (reason) {
    if (sequence === loadSequence) {
      error.value = reason instanceof Error ? reason.message : 'Không tải được lịch sử.'
    }
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    loadSequence += 1
    if (open) void loadConversations()
  },
)
</script>

<template>
  <UiDialog
    :open="open"
    :title="`${agentName} · Lịch sử trò chuyện`"
    description="Lịch sử hội thoại ở đây chỉ đọc; tính năng quản lý/xóa sẽ có sau."
    size="medium"
    @close="$emit('close')"
  >
    <p v-if="loading" class="empty-state">Đang tải lịch sử trò chuyện...</p>
    <div v-else-if="error" class="history-empty">
      <strong>Không tải được lịch sử</strong>
      <p>{{ error }}</p>
      <button class="button button-outline" type="button" data-testid="history-retry" @click="loadConversations">Thử lại</button>
    </div>
    <div v-else-if="conversations.length === 0" class="history-empty">
      <span class="empty-icon"><MessageSquareText :size="25" /></span>
      <strong>Chưa có lịch sử trò chuyện</strong>
      <p>Các cuộc trò chuyện mới sẽ xuất hiện tại đây.</p>
    </div>
    <div v-else class="history-list">
      <article v-for="conversation in conversations" :key="conversation.id">
        <strong>{{ conversation.title || 'Cuộc trò chuyện' }}</strong>
        <p>{{ conversation.summary || 'Chưa có tóm tắt' }} · {{ conversation.turn_count }} lượt</p>
      </article>
    </div>
    <template #footer><button class="button button-ghost" type="button" @click="$emit('close')">Đóng</button></template>
  </UiDialog>
</template>
