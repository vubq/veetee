<script setup lang="ts">
import { computed, ref } from 'vue'

import AgentCard from '@/components/AgentCard.vue'
import AddDeviceDialog from '@/components/AddDeviceDialog.vue'
import ConfigDialog from '@/components/ConfigDialog.vue'
import DeviceDialog from '@/components/DeviceDialog.vue'
import HistoryDialog from '@/components/HistoryDialog.vue'
import PageToolbar from '@/components/PageToolbar.vue'
import type { AgentSummary } from '@/types/agent'

const query = ref('')
const dialog = ref<'add-device' | 'config' | 'history' | 'devices' | null>(null)
const selectedAgent = ref<AgentSummary | null>(null)

function show(type: 'config' | 'history' | 'devices', agent: AgentSummary) {
  selectedAgent.value = agent
  dialog.value = type
}

const agents: AgentSummary[] = [
  {
    id: 'VT-A7F2',
    name: 'Trợ lý chưa đặt tên',
    role: 'Giọng nữ (Female Voice)',
    model: 'Veetee Lite',
    lastConversation: '20 ngày trước',
    deviceCount: 1,
    online: false,
  },
  {
    id: 'VT-C9D4',
    name: 'Trợ lý chưa đặt tên',
    role: 'Giọng nữ (Female Voice)',
    model: 'Veetee Lite',
    lastConversation: '15 giờ trước',
    deviceCount: 1,
    online: true,
  },
]

const filteredAgents = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('vi')
  if (!keyword) return agents
  return agents.filter((agent) =>
    [agent.name, agent.id, agent.role, agent.model].some((value) =>
      value.toLocaleLowerCase('vi').includes(keyword),
    ),
  )
})
</script>

<template>
  <main class="page-container main-content">
    <PageToolbar v-model:query="query" :count="filteredAgents.length" @add-device="dialog = 'add-device'" />
    <section class="agent-grid" aria-label="Danh sách trợ lý">
      <AgentCard v-for="agent in filteredAgents" :key="agent.id" :agent="agent" @configure="show('config', agent)" @history="show('history', agent)" @devices="show('devices', agent)" />
      <div v-if="filteredAgents.length === 0" class="empty-state">
        <h2>Không tìm thấy trợ lý</h2>
        <p>Thử một tên, mã trợ lý hoặc mô hình khác.</p>
      </div>
    </section>
    <AddDeviceDialog :open="dialog === 'add-device'" @close="dialog = null" />
    <ConfigDialog :open="dialog === 'config'" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <HistoryDialog :open="dialog === 'history'" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <DeviceDialog :open="dialog === 'devices'" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
  </main>
</template>
