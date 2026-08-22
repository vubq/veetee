<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import AgentCard from '@/components/AgentCard.vue'
import AgentRenameDialog from '@/components/AgentRenameDialog.vue'
import AddDeviceDialog from '@/components/AddDeviceDialog.vue'
import CreateAgentDialog from '@/components/CreateAgentDialog.vue'
import DeviceDialog from '@/components/DeviceDialog.vue'
import HistoryDialog from '@/components/HistoryDialog.vue'
import PageToolbar from '@/components/PageToolbar.vue'
import UiDialog from '@/components/UiDialog.vue'
import type { AgentSummary } from '@/types/agent'
import { deleteAgent, listAgents, listConversations, listDevices } from '@/api/controlPlane'

const query = ref('')
const dialog = ref<'add-device' | 'create-agent' | 'history' | 'devices' | null>(null)
const selectedAgent = ref<AgentSummary | null>(null)
const addDeviceAgentId = ref<string | null>(null)

const agents = ref<AgentSummary[]>([])
const loading = ref(true)
const loadError = ref('')

const renameTarget = ref<AgentSummary | null>(null)
const deleteTarget = ref<AgentSummary | null>(null)
const deleting = ref(false)
const deleteError = ref('')

async function loadAgents() {
  loading.value = true
  loadError.value = ''
  try {
    // Lỗi /conversations chỉ được làm suy giảm metadata, không được xóa trắng agents/devices.
    const conversationsResult = listConversations().catch(() => [])
    const [agentResult, devices, conversations] = await Promise.all([
      listAgents(),
      listDevices(),
      conversationsResult,
    ])
    agents.value = agentResult.map((agent) => ({
      ...agent,
      deviceCount: devices.filter((device) => device.agent_id === agent.id).length,
      online: devices.some((device) => device.agent_id === agent.id && device.online),
      lastConversation: conversations
        .filter((conversation) => conversation.agent_id === agent.id)
        .sort((left, right) => right.started_at.localeCompare(left.started_at))[0]?.title || 'Chưa có dữ liệu',
    }))
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'Không tải được danh sách trợ lý.'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadAgents()
})

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

function show(type: 'history' | 'devices', agent: AgentSummary) {
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

function addCreatedAgent(agent: AgentSummary) {
  agents.value.push(agent)
}

function applyRenamed(updated: AgentSummary) {
  const index = agents.value.findIndex((agent) => agent.id === updated.id)
  if (index >= 0) agents.value[index] = updated
}

async function confirmDelete() {
  if (!deleteTarget.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await deleteAgent(deleteTarget.value.id)
    agents.value = agents.value.filter((agent) => agent.id !== deleteTarget.value?.id)
    deleteTarget.value = null
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : 'Không xóa được trợ lý.'
  } finally {
    deleting.value = false
  }
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

    <p v-if="loading" class="empty-state">Đang tải danh sách trợ lý...</p>
    <div v-else-if="loadError" class="empty-state">
      <h2>Không tải được dữ liệu</h2>
      <p>{{ loadError }}</p>
      <button class="button button-outline" type="button" data-testid="agents-retry" @click="loadAgents">Thử lại</button>
    </div>
    <template v-else>
      <section class="agent-grid" aria-label="Danh sách trợ lý">
        <AgentCard
          v-for="agent in filteredAgents"
          :key="`${agent.id}:${agent.version ?? 0}`"
          :agent="agent"
          @history="show('history', agent)"
          @devices="show('devices', agent)"
          @rename="renameTarget = agent"
          @delete="deleteTarget = agent"
        />
        <div v-if="filteredAgents.length === 0" class="empty-state">
          <h2>{{ agents.length === 0 ? 'Chưa có trợ lý nào' : 'Không tìm thấy trợ lý' }}</h2>
          <p>{{ agents.length === 0 ? 'Tạo trợ lý đầu tiên để bắt đầu.' : 'Thử một tên, mã trợ lý hoặc mô hình khác.' }}</p>
        </div>
      </section>
    </template>

    <AddDeviceDialog :open="dialog === 'add-device'" :agents="agents" :initial-agent-id="addDeviceAgentId" @close="closeAddDevice" @bound="deviceChanged" />
    <CreateAgentDialog :open="dialog === 'create-agent'" @close="dialog = null" @created="addCreatedAgent" />
    <HistoryDialog :open="dialog === 'history'" :agent-id="selectedAgent?.id" :agent-name="selectedAgent?.name ?? ''" @close="dialog = null" />
    <DeviceDialog :open="dialog === 'devices'" :agent="selectedAgent" @close="dialog = null" @changed="deviceChanged" />

    <AgentRenameDialog :open="Boolean(renameTarget)" :agent="renameTarget" @close="renameTarget = null" @renamed="applyRenamed" />

    <UiDialog
      :open="Boolean(deleteTarget)"
      title="Xóa trợ lý?"
      :description="`Trợ lý “${deleteTarget?.name ?? ''}” sẽ bị xóa vĩnh viễn khỏi hệ thống.`"
      variant="compact"
      @close="deleting ? undefined : (deleteTarget = null)"
    >
      <p class="confirmation-copy">Hành động này không thể hoàn tác. Thiết bị đang liên kết với trợ lý này sẽ bị gỡ khỏi trợ lý.</p>
      <p v-if="deleteError" class="form-message error-message" role="alert">{{ deleteError }}</p>
      <template #footer>
        <button class="button button-outline" type="button" :disabled="deleting" data-testid="delete-cancel" @click="deleteTarget = null">Giữ lại</button>
        <button class="button button-danger" type="button" :disabled="deleting" data-testid="delete-confirm" @click="confirmDelete">{{ deleting ? 'Đang xóa...' : 'Xóa trợ lý' }}</button>
      </template>
    </UiDialog>
  </main>
</template>
