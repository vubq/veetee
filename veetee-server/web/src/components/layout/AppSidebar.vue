<script setup>
import { Activity, Bot, Cpu, Gauge, MessageCircleMore, RadioTower, SlidersHorizontal } from '@lucide/vue'

defineProps({
  modelValue: { type: String, default: 'overview' },
  diagnostics: { type: Object, default: null },
  counts: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const controlItems = [
  { id: 'overview', label: 'Mission Control', hint: 'Tổng quan hệ thống', icon: Gauge },
  { id: 'console', label: 'Voice Console', hint: 'Realtime voice lab', icon: MessageCircleMore },
]
const manageItems = [
  { id: 'assistants', label: 'Assistants', hint: 'Persona & model', icon: Bot },
  { id: 'devices', label: 'Devices', hint: 'ESP32 & pairing', icon: Cpu },
  { id: 'runtime', label: 'Runtime', hint: 'Providers & secrets', icon: SlidersHorizontal },
]
</script>

<template>
  <aside class="studio-sidebar">
    <div class="studio-brand">
      <div class="studio-brand__mark"><RadioTower :size="20" /></div>
      <div>
        <strong>VeeTee</strong>
        <span>Robot AI OS</span>
      </div>
    </div>

    <div class="studio-nav-group">
      <span class="studio-nav-group__label">Control</span>
      <nav class="studio-nav">
        <button
          v-for="item in controlItems"
          :key="item.id"
          type="button"
          class="studio-nav__item"
          :class="{ 'studio-nav__item--active': modelValue === item.id }"
          @click="emit('update:modelValue', item.id)"
        >
          <span class="studio-nav__icon"><component :is="item.icon" :size="17" /></span>
          <span class="studio-nav__copy"><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span>
        </button>
      </nav>
    </div>

    <div class="studio-nav-group">
      <span class="studio-nav-group__label">Manage</span>
      <nav class="studio-nav">
        <button
          v-for="item in manageItems"
          :key="item.id"
          type="button"
          class="studio-nav__item"
          :class="{ 'studio-nav__item--active': modelValue === item.id }"
          @click="emit('update:modelValue', item.id)"
        >
          <span class="studio-nav__icon"><component :is="item.icon" :size="17" /></span>
          <span class="studio-nav__copy"><strong>{{ item.label }}</strong><small>{{ item.hint }}</small></span>
          <span v-if="item.id === 'assistants' && counts.assistants" class="studio-nav__count">{{ counts.assistants }}</span>
          <span v-if="item.id === 'devices' && counts.pending" class="studio-nav__count studio-nav__count--warning">{{ counts.pending }}</span>
        </button>
      </nav>
    </div>

    <div class="studio-sidebar__runtime">
      <div class="studio-sidebar__runtime-head">
        <span><Activity :size="14" /></span>
        <div><strong>Runtime live</strong><small>{{ diagnostics?.server?.active_sessions ?? 0 }} active sessions</small></div>
      </div>
      <dl>
        <div><dt>LLM</dt><dd>{{ diagnostics?.llm?.model || '—' }}</dd></div>
        <div><dt>Voice</dt><dd>{{ diagnostics?.tts?.voice || '—' }}</dd></div>
      </dl>
    </div>
  </aside>
</template>