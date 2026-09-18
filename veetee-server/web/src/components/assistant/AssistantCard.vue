<script setup>
import { Bot, Cpu, Pencil, Power, Trash2 } from '@lucide/vue'
import UiBadge from '../ui/UiBadge.vue'
import UiButton from '../ui/UiButton.vue'

defineProps({ item: { type: Object, required: true } })
defineEmits(['edit', 'toggle', 'delete'])
</script>

<template>
  <article class="assistant-row">
    <div class="assistant-row__identity">
      <div class="assistant-row__avatar"><Bot :size="18" /></div>
      <div>
        <div class="assistant-row__name">
          <strong>{{ item.name }}</strong>
          <UiBadge :tone="item.enabled !== false ? 'success' : 'neutral'" dot>{{ item.enabled !== false ? 'Active' : 'Disabled' }}</UiBadge>
        </div>
        <span>{{ item.id }}</span>
      </div>
    </div>

    <div class="assistant-row__persona">
      <span>PERSONA</span>
      <p>{{ item.base_prompt || 'Chưa có persona riêng. Đang dùng prompt mặc định của hệ thống.' }}</p>
    </div>

    <div class="assistant-row__config">
      <div><span>VOICE</span><strong>{{ item.voice || 'Global default' }}</strong></div>
      <div><span>MODEL</span><strong>{{ item.model || 'Global default' }}</strong></div>
      <div><span>DEVICES</span><strong><Cpu :size="12" /> {{ item.device_count || 0 }}</strong></div>
    </div>

    <div class="assistant-row__actions">
      <UiButton variant="secondary" size="sm" @click="$emit('edit', item)"><template #icon><Pencil :size="13" /></template>Sửa</UiButton>
      <UiButton variant="ghost" size="sm" @click="$emit('toggle', item)"><template #icon><Power :size="13" /></template>{{ item.enabled !== false ? 'Tắt' : 'Bật' }}</UiButton>
      <UiButton variant="danger-ghost" size="sm" icon-only aria-label="Xóa Assistant" @click="$emit('delete', item)"><template #icon><Trash2 :size="14" /></template></UiButton>
    </div>
  </article>
</template>