<script setup>
import { computed, ref } from 'vue'
import { Bot, ChevronDown, Cpu, Plus, Search, X } from '@lucide/vue'
import AssistantCard from '../components/assistant/AssistantCard.vue'
import UiEmptyState from '../components/ui/UiEmptyState.vue'

const props = defineProps({ assistants: { type: Array, default: () => [] } })
const emit = defineEmits(['create', 'edit', 'toggle', 'delete', 'add-device'])

const query = ref('')
const menuOpen = ref(false)

const filteredAssistants = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.assistants
  return props.assistants.filter(item => [
    item.name,
    item.model,
    item.voice,
    item.base_prompt,
  ].some(value => String(value || '').toLocaleLowerCase().includes(needle)))
})

function createAssistant() {
  menuOpen.value = false
  emit('create')
}
</script>

<template>
  <div class="workspace-view assistants-view xz-agents">
    <section class="xz-agents-toolbar">
      <div class="xz-agents-toolbar__glow"></div>
      <div class="xz-agents-toolbar__inner">
        <div class="xz-agents-heading">
          <span class="xz-agents-heading__icon"><Bot :size="20" /></span>
          <div>
            <h1>Assistants</h1>
            <p>{{ assistants.length }} assistants</p>
          </div>
        </div>

        <div class="xz-agents-actions">
          <label class="xz-search">
            <Search :size="16" />
            <input v-model="query" type="search" placeholder="Tìm Assistant" aria-label="Tìm Assistant" />
            <button v-if="query" type="button" aria-label="Xóa tìm kiếm" @click="query = ''"><X :size="15" /></button>
          </label>

          <div class="xz-split-action">
            <button type="button" class="xz-primary-action" @click="emit('add-device')">
              <Plus :size="16" />
              <span>Thêm thiết bị</span>
            </button>
            <button
              type="button"
              class="xz-primary-action xz-primary-action--menu"
              aria-label="Mở menu tạo Assistant"
              :aria-expanded="menuOpen"
              @click="menuOpen = !menuOpen"
            >
              <ChevronDown :size="16" />
            </button>
            <div v-if="menuOpen" class="xz-action-menu">
              <button type="button" @click="createAssistant">
                <Plus :size="16" />
                Tạo Assistant
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div v-if="filteredAssistants.length" class="xz-agent-grid">
      <AssistantCard
        v-for="item in filteredAssistants"
        :key="item.id"
        :item="item"
        @edit="$emit('edit', $event)"
        @toggle="$emit('toggle', $event)"
        @delete="$emit('delete', $event)"
      />
    </div>

    <UiEmptyState
      v-else
      :title="query ? 'Không tìm thấy Assistant' : 'Chưa có Assistant'"
      :description="query ? 'Thử một từ khóa khác.' : 'Tạo Assistant đầu tiên để gán persona, voice và model cho robot.'"
      class="xz-empty-state"
    >
      <template #icon><Cpu v-if="query" :size="24" /><Bot v-else :size="24" /></template>
      <button v-if="!query" type="button" class="xz-empty-primary" @click="emit('create')"><Plus :size="16" />Tạo Assistant</button>
    </UiEmptyState>
  </div>
</template>
