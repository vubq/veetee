<script setup>
import { Bot, UserRound } from '@lucide/vue'

defineProps({
  role: { type: String, default: 'assistant' },
  text: { type: String, default: '' },
})
</script>

<template>
  <div class="chat-message" :class="`chat-message--${role}`">
    <div class="chat-message__avatar">
      <component :is="role === 'user' ? UserRound : Bot" :size="14" />
    </div>
    <div class="chat-message__body">
      <span>{{ role === 'user' ? 'YOU' : 'VEETEE' }}</span>
      <p>{{ text || '…' }}</p>
    </div>
  </div>
</template>