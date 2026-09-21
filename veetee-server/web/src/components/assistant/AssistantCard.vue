<script setup>
import { computed } from 'vue'
import { Bot, Cpu, Pencil, Power, Trash2, Volume2, Workflow } from '@lucide/vue'

const props = defineProps({ item: { type: Object, required: true } })
defineEmits(['edit', 'toggle', 'delete'])

const initial = computed(() => Array.from(String(props.item.name || '?').trim())[0]?.toLocaleUpperCase() || '?')
const avatarTone = computed(() => {
  const value = String(props.item.id || props.item.name || '')
  let sum = 0
  for (const char of value) sum += char.codePointAt(0) || 0
  return sum % 6
})
</script>

<template>
  <article class="xz-agent-card">
    <div class="xz-agent-card__top">
      <div class="xz-agent-card__identity">
        <span class="xz-agent-avatar" :class="'xz-agent-avatar--' + avatarTone" aria-hidden="true">
          <i></i>
          <strong>{{ initial }}</strong>
        </span>
        <div class="xz-agent-card__name">
          <div>
            <h2 :title="item.name">{{ item.name }}</h2>
            <span class="xz-agent-status" :class="{ 'xz-agent-status--disabled': item.enabled === false }">
              {{ item.enabled !== false ? 'Active' : 'Disabled' }}
            </span>
          </div>
          <small>{{ item.id }}</small>
        </div>
      </div>

      <button type="button" class="xz-card-icon-button" aria-label="Sửa Assistant" @click="$emit('edit', item)">
        <Pencil :size="16" />
      </button>
    </div>

    <div class="xz-agent-metrics">
      <div>
        <span><Volume2 :size="12" /> Voice</span>
        <strong :title="item.voice || 'Global default'">{{ item.voice || 'Global default' }}</strong>
      </div>
      <div>
        <span><Workflow :size="12" /> Model</span>
        <strong :title="item.model || 'Global default'">{{ item.model || 'Global default' }}</strong>
      </div>
      <div>
        <span><Cpu :size="12" /> Devices</span>
        <strong>{{ item.device_count || 0 }}</strong>
      </div>
    </div>

    <div class="xz-agent-card__footer">
      <button type="button" @click="$emit('edit', item)">
        <Bot :size="15" />
        <span>Configure</span>
      </button>
      <button type="button" @click="$emit('toggle', item)">
        <Power :size="15" />
        <span>{{ item.enabled !== false ? 'Disable' : 'Enable' }}</span>
      </button>
      <button type="button" class="xz-agent-card__danger" @click="$emit('delete', item)">
        <Trash2 :size="15" />
        <span>Delete</span>
      </button>
    </div>
  </article>
</template>
