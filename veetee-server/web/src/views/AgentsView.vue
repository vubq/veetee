<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AgentCard from '@/components/AgentCard.vue'
import AddDeviceDialog from '@/components/AddDeviceDialog.vue'
import ConfigDialog from '@/components/ConfigDialog.vue'
import DeviceDialog from '@/components/DeviceDialog.vue'
import HistoryDialog from '@/components/HistoryDialog.vue'
import PageToolbar from '@/components/PageToolbar.vue'
import type { AgentSummary } from '@/types/agent'
import { listAgents, login } from '@/api/controlPlane'

const query = ref('')
const dialog = ref<'add-device' | 'config' | 'history' | 'devices' | null>(null)
const selectedAgent = ref<AgentSummary | null>(null)

function show(type: 'config' | 'history' | 'devices', agent: AgentSummary) {
  selectedAgent.value = agent
  dialog.value = type
}

const agents = ref<AgentSummary[]>([
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
])
const loading = ref(true)
const loadError = ref('')
const email = ref('')
const password = ref('')
const authenticated = ref(false)

async function loadAgents() {
  agents.value = await listAgents()
  authenticated.value = true
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

onMounted(async () => {
  try {
    await loadAgents()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Cần đăng nhập để tải danh sách trợ lý.'
  } finally {
    loading.value = false
  }
})

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
    <PageToolbar v-model:query="query" :count="filteredAgents.length" @add-device="dialog = 'add-device'" />
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
    <AddDeviceDialog :open="dialog === 'add-device'" @close="dialog = null" />
    <ConfigDialog :open="dialog === 'config'" :agent="selectedAgent" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <HistoryDialog :open="dialog === 'history'" :agent-id="selectedAgent?.id" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <DeviceDialog :open="dialog === 'devices'" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
  </main>
</template>
