<script setup lang="ts">
import { computed, ref } from 'vue'

import AgentCard from '@/components/AgentCard.vue'
import AddDeviceDialog from '@/components/AddDeviceDialog.vue'
import ConfigDialog from '@/components/ConfigDialog.vue'
import CreateAgentDialog from '@/components/CreateAgentDialog.vue'
import DeviceDialog from '@/components/DeviceDialog.vue'
import HistoryDialog from '@/components/HistoryDialog.vue'
import PageToolbar from '@/components/PageToolbar.vue'
import type { AgentSummary } from '@/types/agent'
import { listAgents, type DeviceSummary } from '@/api/controlPlane'

const query = ref('')
const dialog = ref<'add-device' | 'create-agent' | 'config' | 'history' | 'devices' | null>(null)
const selectedAgent = ref<AgentSummary | null>(null)

function show(type: 'config' | 'history' | 'devices', agent: AgentSummary) {
  selectedAgent.value = agent
  dialog.value = type
}

const agents = ref<AgentSummary[]>([])
const loading = ref(true)
const loadError = ref('')

async function loadAgents() {
  loading.value = true
  loadError.value = ''
  try {
    agents.value = await listAgents()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Đăng nhập thất bại.'
  } finally {
    loading.value = false
  }
}

void loadAgents()

function addCreatedAgent(agent: AgentSummary) {
  agents.value.push(agent)
}

function bound(device: DeviceSummary) {
  dialog.value = null
  const agent = agents.value.find((item) => item.id === device.agent_id)
  if (agent) agent.deviceCount += 1
}

function requestBind() {
  dialog.value = 'add-device'
}

function addDeviceFromToolbar() {
  selectedAgent.value = null
  dialog.value = 'add-device'
}

const filteredAgents = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('vi')
  if (!keyword) return agents.value
  return agents.value.filter((agent) =>
    [agent.name, agent.id, agent.role, agent.model].some((value) =>
      value.toLocaleLowerCase('vi').includes(keyword),
    ),
  )
})
</script>

<template>
  <main class="page-container main-content">
    <PageToolbar v-model:query="query" :count="filteredAgents.length" @add-device="addDeviceFromToolbar" @create-agent="dialog = 'create-agent'" />
    <p v-if="loading" class="empty-state">Đang tải danh sách trợ lý...</p>
    <p v-else-if="loadError" class="empty-state">{{ loadError }}</p>
    <section v-else class="agent-grid" aria-label="Danh sách trợ lý">
      <AgentCard v-for="agent in filteredAgents" :key="agent.id" :agent="agent" @configure="show('config', agent)" @history="show('history', agent)" @devices="show('devices', agent)" />
      <div v-if="filteredAgents.length === 0" class="empty-state">
        <h2>Không tìm thấy trợ lý</h2>
        <p>Thử một tên, mã trợ lý hoặc mô hình khác.</p>
      </div>
    </section>
    <AddDeviceDialog :open="dialog === 'add-device'" :agents="agents" :default-agent-id="selectedAgent?.id" @close="dialog = null" @bound="bound" />
    <CreateAgentDialog :open="dialog === 'create-agent'" @close="dialog = null" @created="addCreatedAgent" />
    <ConfigDialog :open="dialog === 'config'" :agent="selectedAgent" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <HistoryDialog :open="dialog === 'history'" :agent-id="selectedAgent?.id" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <DeviceDialog :open="dialog === 'devices'" :agent-id="selectedAgent?.id ?? ''" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" @request-bind="requestBind" />
  </main>
</template>
