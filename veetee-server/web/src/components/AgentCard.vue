<script setup lang="ts">
import { Clock3, Cpu, Ellipsis, History, Pencil, Settings2, Sparkles, Trash2, UserRound } from '@lucide/vue'

import UiDropdown, { type DropdownItem } from '@/components/UiDropdown.vue'
import type { AgentSummary } from '@/types/agent'

defineProps<{
  agent: AgentSummary
}>()

const emit = defineEmits<{ history: []; devices: []; configure: []; rename: []; delete: [] }>()

const menuItems: DropdownItem[] = [
  { label: 'Đổi tên', value: 'rename', icon: Pencil },
  { label: 'Xóa trợ lý', value: 'delete', danger: true, icon: Trash2 },
]

function handleMenuSelect(value: string) {
  if (value === 'rename') emit('rename')
  else if (value === 'delete') emit('delete')
}
</script>

<template>
  <article class="agent-card">
    <div class="agent-card-header">
      <div class="agent-identity">
        <div class="agent-avatar">
          <span>T</span>
        </div>
        <div class="agent-name-block">
          <div class="agent-title-row">
            <h2>{{ agent.name }}</h2>
            <span class="status-badge" :class="{ offline: !agent.online }"><i></i>{{ agent.online ? 'Trực tuyến' : 'Ngoại tuyến' }}</span>
          </div>
        </div>
      </div>
      <UiDropdown label="Thao tác" :items="menuItems" @select="handleMenuSelect">
        <template #trigger><button class="card-menu" type="button" aria-label="Thao tác" title="Thao tác"><Ellipsis :size="18" /></button></template>
      </UiDropdown>
    </div>

    <dl class="agent-stats">
      <div>
        <dt><UserRound :size="15" />Vai trò</dt>
        <dd>{{ agent.role || 'Chưa đặt vai trò' }}</dd>
      </div>
      <div>
        <dt><Sparkles :size="15" />Mô hình</dt>
        <dd>{{ agent.model || 'Mặc định hệ thống' }}</dd>
      </div>
      <div>
        <dt><Clock3 :size="15" />Cuộc trò chuyện gần nhất</dt>
        <dd>{{ agent.lastConversation }}</dd>
      </div>
    </dl>

    <div class="agent-actions">
      <button type="button" @click="emit('history')"><History :size="16" />Lịch sử</button>
      <button type="button" @click="emit('devices')"><Cpu :size="16" />Thiết bị ({{ agent.deviceCount }})</button>
      <button type="button" data-testid="agent-configure" @click="emit('configure')"><Settings2 :size="16" />Cấu hình</button>
    </div>
  </article>
</template>
