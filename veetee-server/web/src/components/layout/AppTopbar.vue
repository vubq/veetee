<script setup>
import { computed } from 'vue'
import { RefreshCw } from '@lucide/vue'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'

const props = defineProps({
  activeView: { type: String, default: 'overview' },
  health: { type: Object, default: null },
  loading: Boolean,
})
defineEmits(['refresh'])

const meta = computed(() => ({
  overview: { section: 'CONTROL', title: 'Mission Control' },
  console: { section: 'LAB', title: 'Voice Console' },
  assistants: { section: 'IDENTITY', title: 'Assistants' },
  devices: { section: 'HARDWARE', title: 'Devices & Pairing' },
  runtime: { section: 'SYSTEM', title: 'Runtime & Secrets' },
}[props.activeView] || { section: 'VEETEE', title: 'Workspace' }))

const healthTone = computed(() => {
  if (!props.health) return 'neutral'
  return props.health.readiness === 'ready' ? 'success' : 'warning'
})
const healthLabel = computed(() => {
  if (!props.health) return 'checking'
  const reasons = props.health.degraded_reasons || []
  if (props.health.readiness === 'degraded' && reasons.includes('llm_not_warm')) {
    return 'degraded · LLM unavailable'
  }
  return props.health.status || props.health.readiness || 'unknown'
})
</script>

<template>
  <header class="workspace-bar">
    <div class="workspace-bar__heading">
      <span>{{ meta.section }}</span>
      <strong>{{ meta.title }}</strong>
    </div>

    <div class="workspace-bar__actions">
      <UiBadge :tone="healthTone" dot>
        {{ healthLabel }}
      </UiBadge>
      <UiButton variant="ghost" size="sm" :loading="loading" @click="$emit('refresh')">
        <template #icon><RefreshCw :size="14" /></template>
        Đồng bộ
      </UiButton>
    </div>
  </header>
</template>