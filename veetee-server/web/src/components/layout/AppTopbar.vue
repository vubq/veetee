<script setup>
import { computed } from 'vue'
import {
  Activity,
  Bot,
  Cpu,
  LayoutDashboard,
  MessageCircleMore,
  RefreshCw,
  SlidersHorizontal,
} from '@lucide/vue'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'

const props = defineProps({
  activeView: { type: String, default: 'assistants' },
  health: { type: Object, default: null },
  loading: Boolean,
  counts: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['refresh', 'navigate'])

const navItems = [
  { id: 'assistants', label: 'Assistants', icon: Bot },
  { id: 'console', label: 'Voice Console', icon: MessageCircleMore },
  { id: 'devices', label: 'Devices', icon: Cpu },
  { id: 'runtime', label: 'Runtime', icon: SlidersHorizontal },
]

const healthTone = computed(() => {
  if (!props.health) return 'neutral'
  return props.health.readiness === 'ready' ? 'success' : 'warning'
})
const healthLabel = computed(() => {
  if (!props.health) return 'checking'
  const reasons = props.health.degraded_reasons || []
  if (props.health.readiness === 'degraded' && reasons.includes('llm_not_warm')) return 'degraded'
  return props.health.status || props.health.readiness || 'unknown'
})
</script>

<template>
  <header class="xz-header">
    <div class="xz-header__inner">
      <button type="button" class="xz-brand" aria-label="VeeTee overview" @click="emit('navigate', 'overview')">
        <span class="xz-brand__mark"><LayoutDashboard :size="18" /></span>
        <span class="xz-brand__name">VeeTee</span>
      </button>

      <nav class="xz-nav" aria-label="Main navigation">
        <button
          v-for="item in navItems"
          :key="item.id"
          type="button"
          class="xz-nav__item"
          :class="{ 'xz-nav__item--active': activeView === item.id }"
          @click="emit('navigate', item.id)"
        >
          <component :is="item.icon" :size="16" />
          <span>{{ item.label }}</span>
          <small v-if="item.id === 'assistants' && counts.assistants">{{ counts.assistants }}</small>
          <small v-if="item.id === 'devices' && counts.pending" class="xz-nav__alert">{{ counts.pending }}</small>
        </button>
      </nav>

      <div class="xz-header__actions">
        <UiBadge :tone="healthTone" dot class="xz-health">{{ healthLabel }}</UiBadge>
        <UiButton
          variant="ghost"
          size="sm"
          icon-only
          :loading="loading"
          aria-label="Đồng bộ"
          title="Đồng bộ"
          @click="emit('refresh')"
        >
          <template #icon><RefreshCw :size="16" /></template>
        </UiButton>
        <span class="xz-runtime-dot" :class="{ 'xz-runtime-dot--ready': health?.readiness === 'ready' }" title="Runtime">
          <Activity :size="15" />
        </span>
      </div>
    </div>
  </header>
</template>
