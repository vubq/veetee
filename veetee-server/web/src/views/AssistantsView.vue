<script setup>
import { Bot, Plus, Sparkles } from '@lucide/vue'
import AssistantCard from '../components/assistant/AssistantCard.vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiEmptyState from '../components/ui/UiEmptyState.vue'

defineProps({ assistants: { type: Array, default: () => [] } })
defineEmits(['create', 'edit', 'toggle', 'delete'])
</script>

<template>
  <div class="workspace-view assistants-view">
    <PageHeader eyebrow="03 / Identity" title="Assistants" description="Quản lý các identity profile độc lập: persona, voice, model và thiết bị đang bind.">
      <template #actions>
        <UiButton variant="primary" @click="$emit('create')"><template #icon><Plus :size="15" /></template>Tạo Assistant</UiButton>
      </template>
    </PageHeader>

    <div class="assistant-workspace">
      <aside class="assistant-summary">
        <div class="assistant-summary__icon"><Sparkles :size="20" /></div>
        <span>IDENTITY LAYER</span>
        <strong>{{ assistants.length }}</strong>
        <h2>Assistant profiles</h2>
        <p>Mỗi profile có persona, voice và model riêng. Device nhận cấu hình khi bind hoặc reconnect.</p>
        <button type="button" @click="$emit('create')"><Plus :size="14" /> Thêm Assistant mới</button>
      </aside>

      <section class="assistant-list-panel">
        <div class="assistant-list-panel__head">
          <div><span>PROFILES</span><strong>Danh sách Assistant</strong></div>
          <small>{{ assistants.length }} total</small>
        </div>

        <div v-if="assistants.length" class="assistant-list">
          <AssistantCard
            v-for="item in assistants"
            :key="item.id"
            :item="item"
            @edit="$emit('edit', $event)"
            @toggle="$emit('toggle', $event)"
            @delete="$emit('delete', $event)"
          />
        </div>

        <UiEmptyState v-else title="Chưa có Assistant" description="Tạo Assistant đầu tiên để gán persona, voice và model cho robot.">
          <template #icon><Bot :size="24" /></template>
          <UiButton variant="primary" @click="$emit('create')"><template #icon><Plus :size="15" /></template>Tạo Assistant</UiButton>
        </UiEmptyState>
      </section>
    </div>
  </div>
</template>