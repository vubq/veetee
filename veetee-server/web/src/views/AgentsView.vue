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
import { listAgents, listDevices, login } from '@/api/controlPlane'

const query = ref('')
const dialog = ref<'add-device' | 'create-agent' | 'config' | 'history' | 'devices' | null>(null)
const selectedAgent = ref<AgentSummary | null>(null)
const addDeviceAgentId = ref<string | null>(null)

function show(type: 'config' | 'history' | 'devices', agent: AgentSummary) {
  selectedAgent.value = agent
  dialog.value = type
}

function showAddDevice(agentId: string | null = null) {
  addDeviceAgentId.value = agentId
  dialog.value = 'add-device'
}

function closeAddDevice() {
  dialog.value = null
  addDeviceAgentId.value = null
}

const agents = ref<AgentSummary[]>([])
const loading = ref(true)
const loadError = ref('')
const email = ref('')
const password = ref('')
const authenticated = ref(false)

async function loadAgents() {
  const [agentResult, devices] = await Promise.all([listAgents(), listDevices()])
  agents.value = agentResult.map((agent) => ({
    ...agent,
    deviceCount: devices.filter((device) => device.agent_id === agent.id).length,
    online: devices.some((device) => device.agent_id === agent.id && device.online),
  }))
  authenticated.value = true
}

async function refreshDeviceMetadata() {
  const devices = await listDevices()
  for (const agent of agents.value) {
    agent.deviceCount = devices.filter((device) => device.agent_id === agent.id).length
    agent.online = devices.some((device) => device.agent_id === agent.id && device.online)
  }
}

async function deviceChanged() {
  try {
    await refreshDeviceMetadata()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Không thể làm mới danh sách thiết bị.'
  }
}

async function signIn() {
  loading.value = true
  loadError.value = ''
  try {
    await login(email.value, password.value)
    await loadAgents()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Đăng nhập thất bại.'
  } finally {
    loading.value = false
  }
}

loading.value = false

function addCreatedAgent(agent: AgentSummary) {
  agents.value.push(agent)
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
    <PageToolbar v-model:query="query" :count="filteredAgents.length" :agent-count="agents.length" @add-device="showAddDevice()" @create-agent="dialog = 'create-agent'" />
    <form v-if="!authenticated" class="empty-state" @submit.prevent="signIn">
      <h2>Đăng nhập Console</h2>
      <p>Phiên đăng nhập chỉ được giữ trong bộ nhớ trình duyệt.</p>
      <input v-model="email" type="email" placeholder="Email" required autocomplete="username" />
      <input v-model="password" type="password" placeholder="Mật khẩu" required autocomplete="current-password" />
      <button class="button button-primary" type="submit" :disabled="loading">Đăng nhập</button>
      <p v-if="loadError">{{ loadError }}</p>
    </form>
    <p v-if="loading" class="empty-state">Đang tải danh sách trợ lý...</p>
    <p v-else-if="loadError && authenticated" class="empty-state">{{ loadError }}</p>
    <section class="agent-grid" aria-label="Danh sách trợ lý">
      <AgentCard v-for="agent in filteredAgents" :key="agent.id" :agent="agent" @configure="show('config', agent)" @history="show('history', agent)" @devices="show('devices', agent)" />
      <div v-if="filteredAgents.length === 0" class="empty-state">
        <h2>Không tìm thấy trợ lý</h2>
        <p>Thử một tên, mã trợ lý hoặc mô hình khác.</p>
      </div>
    </section>
    <AddDeviceDialog :open="dialog === 'add-device'" :agents="agents" :initial-agent-id="addDeviceAgentId" @close="closeAddDevice" @bound="deviceChanged" />
    <CreateAgentDialog :open="dialog === 'create-agent'" @close="dialog = null" @created="addCreatedAgent" />
    <ConfigDialog :open="dialog === 'config'" :agent="selectedAgent" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <HistoryDialog :open="dialog === 'history'" :agent-id="selectedAgent?.id" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <DeviceDialog :open="dialog === 'devices'" :agent="selectedAgent" @close="dialog = null" @changed="deviceChanged" />
  </main>
</template>
