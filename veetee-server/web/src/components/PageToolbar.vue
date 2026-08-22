<script setup lang="ts">
import { Bot, ChevronDown, FileUp, LayoutTemplate, Plus, Search, Sparkles } from '@lucide/vue'

import UiDropdown, { type DropdownItem } from '@/components/UiDropdown.vue'

defineProps<{
  count: number
}>()

const query = defineModel<string>('query', { required: true })
const emit = defineEmits<{ 'add-device': []; 'create-agent': [] }>()
const createItems: DropdownItem[] = [
  { label: 'Tạo trợ lý mới', value: 'blank', icon: Sparkles },
  { label: 'Tạo từ mẫu · Sắp có', value: 'template', icon: LayoutTemplate, disabled: true },
  { label: 'Nhập cấu hình · Sắp có', value: 'import', icon: FileUp, disabled: true },
]

function selectCreate(value: string) {
  if (value === 'blank') emit('create-agent')
}
</script>

<template>
  <section class="page-toolbar">
    <div class="toolbar-glow" aria-hidden="true"></div>
    <div class="toolbar-inner">
      <div class="page-title-group">
        <span class="page-title-icon" aria-hidden="true"><Bot :size="19" /></span>
        <div>
          <h1>Trợ lý</h1>
          <p>{{ count }} trợ lý</p>
        </div>
      </div>

      <div class="toolbar-actions">
        <label class="search-field">
          <Search :size="16" aria-hidden="true" />
          <input v-model="query" name="agent-search" type="search" aria-label="Tìm trợ lý" placeholder="Tìm trợ lý" />
        </label>
        <UiDropdown label="Tạo trợ lý" :items="createItems" @select="selectCreate">
          <template #trigger><div class="split-button"><button class="button button-primary add-device-button" type="button" @click.stop="emit('add-device')"><Plus :size="16" /><span>Thêm thiết bị</span></button><button class="button button-primary split-trigger" type="button" aria-label="Tạo trợ lý"><ChevronDown :size="16" /></button></div></template>
        </UiDropdown>
      </div>
    </div>
  </section>
</template>
