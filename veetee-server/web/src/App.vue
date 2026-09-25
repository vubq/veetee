<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { AlertTriangle, Bot, KeyRound, Save, ShieldAlert } from '@lucide/vue'
import AppTopbar from './components/layout/AppTopbar.vue'
import UiButton from './components/ui/UiButton.vue'
import UiInput from './components/ui/UiInput.vue'
import UiModal from './components/ui/UiModal.vue'
import UiSelect from './components/ui/UiSelect.vue'
import UiSwitch from './components/ui/UiSwitch.vue'
import UiTextarea from './components/ui/UiTextarea.vue'
import UiToast from './components/ui/UiToast.vue'
import { useDashboard } from './composables/useDashboard'
import { useVoiceConsole } from './composables/useVoiceConsole'
import AssistantsView from './views/AssistantsView.vue'
import DevicesView from './views/DevicesView.vue'
import OverviewView from './views/OverviewView.vue'
import RuntimeView from './views/RuntimeView.vue'
import VoiceConsoleView from './views/VoiceConsoleView.vue'

const activeView = ref('assistants')
const showAssistantEditor = ref(false)
const confirmState = reactive({ open: false, kind: '', item: null, title: '', description: '' })

const dashboard = useDashboard()
const {
  loading, busy, authRequired, managerToken, loginError, loginBusy,
  health, diagnostics, assistants, devices, pendingDevices, runtime, restartRequired, groqKeys, voices, models,
  assistantDraft, pairing, runtimeDraft, toast, pairedActiveDevices, onlineDevices,
  notify, fmtTime, secretPlaceholder, login, loadPublicData, loadManagedData, refreshAll,
  prepareNewAssistant, prepareEditAssistant, saveAssistant, toggleAssistant, deleteAssistant,
  pairDevice, updateDevice, revokeDevice, addGroqKey, updateGroqKey, updateGroqLimit, removeGroqKey, saveRuntime,
} = dashboard

const voice = useVoiceConsole({ authRequired, notify, health })
const {
  chatInput, chatMessages, protocolLog, wsState, protocolVersion, listenMode, rawProtocol,
  useRealAudio, isMicRecording, isSyntheticAudioRunning, audioStatus, firstAudioLatency,
  currentEmotion, pipelineVad, pipelineAsr, pipelineLlm, pipelineTts,
  connectWs, reconnectWs, unlockAudio, sendChat, abortTurn, toggleMic, directTts, testHealth, testOta,
  listenStart, listenStop, wakeDetect, sendEndIntent, sendRawProtocol, sendWs, clearConversation,
} = voice

const sidebarCounts = computed(() => ({ assistants: assistants.value.length, pending: pendingDevices.value.length }))
const voiceOptions = computed(() => [
  { value: '', label: 'Global default', description: 'Dùng voice mặc định của runtime' },
  ...voices.value.map(item => ({ value: item.name, label: item.name, description: item.description || '' })),
])
const modelOptions = computed(() => [
  { value: '', label: 'Global default', description: 'Dùng model mặc định của runtime' },
  ...models.value.map(item => ({ value: item, label: item })),
])

function navigate(view) { activeView.value = view }
function openNewAssistant() {
  prepareNewAssistant()
  showAssistantEditor.value = true
  activeView.value = 'assistants'
}
function openEditAssistant(item) {
  prepareEditAssistant(item)
  showAssistantEditor.value = true
}
async function submitAssistant() {
  if (await saveAssistant()) showAssistantEditor.value = false
}
function updatePairing(key, value) { pairing[key] = value }
function updateRuntime(key, value) { runtimeDraft[key] = value }
function setProtocolVersion(value) { protocolVersion.value = Number(value) }

function askDeleteAssistant(item) {
  Object.assign(confirmState, {
    open: true,
    kind: 'assistant',
    item,
    title: `Xóa “${item.name}”?`,
    description: 'Assistant sẽ bị xóa vĩnh viễn. Thao tác này chỉ thực hiện được khi không còn thiết bị active đang bind.',
  })
}
function askRevokeDevice(item) {
  Object.assign(confirmState, {
    open: true,
    kind: 'device',
    item,
    title: `Revoke “${item.name || item.device_id}”?`,
    description: 'Credential hiện tại sẽ bị vô hiệu hóa và phiên WebSocket đang hoạt động của thiết bị sẽ bị đóng.',
  })
}
async function confirmDangerousAction() {
  const { kind, item } = confirmState
  if (!item) return
  let ok = false
  if (kind === 'assistant') ok = await deleteAssistant(item)
  if (kind === 'device') ok = await revokeDevice(item)
  if (ok) confirmState.open = false
}

onMounted(async () => {
  await loadPublicData()
  await loadManagedData()
})
</script>

<template>
  <div class="studio-shell">
    <section class="studio-main">
      <AppTopbar
        :active-view="activeView"
        :health="health"
        :loading="loading"
        :counts="sidebarCounts"
        @refresh="refreshAll"
        @navigate="navigate"
      />

      <div v-if="loading" class="studio-progress"><span></span></div>

      <main class="studio-content">
        <Transition name="view-fade" mode="out-in">
          <OverviewView
            v-if="activeView === 'overview'"
            key="overview"
            :health="health"
            :diagnostics="diagnostics"
            :assistants="assistants"
            :active-devices="pairedActiveDevices"
            :online-devices="onlineDevices"
            :pending-devices="pendingDevices"
            @navigate="navigate"
            @create-assistant="openNewAssistant"
          />

          <VoiceConsoleView
            v-else-if="activeView === 'console'"
            key="console"
            :chat-input="chatInput"
            :messages="chatMessages"
            :protocol-log="protocolLog"
            :ws-state="wsState"
            :protocol-version="protocolVersion"
            :listen-mode="listenMode"
            :raw-protocol="rawProtocol"
            :use-real-audio="useRealAudio"
            :is-mic-recording="isMicRecording"
            :is-synthetic-audio-running="isSyntheticAudioRunning"
            :audio-status="audioStatus"
            :first-audio-latency="firstAudioLatency"
            :current-emotion="currentEmotion"
            :pipeline-vad="pipelineVad"
            :pipeline-asr="pipelineAsr"
            :pipeline-llm="pipelineLlm"
            :pipeline-tts="pipelineTts"
            :health="health"
            @update:chat-input="chatInput = $event"
            @update:protocol-version="setProtocolVersion"
            @update:listen-mode="listenMode = $event"
            @update:raw-protocol="rawProtocol = $event"
            @update:use-real-audio="useRealAudio = $event"
            @connect="connectWs"
            @reconnect="reconnectWs"
            @unlock-audio="unlockAudio"
            @send="sendChat"
            @toggle-mic="toggleMic"
            @abort="abortTurn"
            @direct-tts="directTts"
            @health="testHealth"
            @ota="testOta"
            @ping="sendWs({ type: 'ping' }, 'PING →')"
            @listen-start="listenStart"
            @listen-stop="listenStop"
            @wake-detect="wakeDetect(false)"
            @wake-start="wakeDetect(true)"
            @end-intent="sendEndIntent"
            @send-raw="sendRawProtocol"
            @clear="clearConversation"
            @runtime="navigate('runtime')"
          />

          <AssistantsView
            v-else-if="activeView === 'assistants'"
            key="assistants"
            :assistants="assistants"
            @create="openNewAssistant"
            @add-device="navigate('devices')"
            @edit="openEditAssistant"
            @toggle="toggleAssistant"
            @delete="askDeleteAssistant"
          />

          <DevicesView
            v-else-if="activeView === 'devices'"
            key="devices"
            :devices="devices"
            :pending-devices="pendingDevices"
            :assistants="assistants"
            :pairing="pairing"
            :busy="busy"
            :fmt-time="fmtTime"
            @pair="pairDevice"
            @update-device="updateDevice"
            @revoke="askRevokeDevice"
            @update-pairing="updatePairing"
          />

          <RuntimeView
            v-else
            key="runtime"
            :draft="runtimeDraft"
            :runtime="runtime"
            :models="models"
            :voices="voices"
            :busy="busy"
            :groq-keys="groqKeys"
            :secret-placeholder="secretPlaceholder"
            @update="updateRuntime"
            @add-groq-key="addGroqKey"
            @update-groq-key="updateGroqKey"
            @update-groq-limit="updateGroqLimit"
            @remove-groq-key="removeGroqKey"
            @save="saveRuntime"
          />
        </Transition>
      </main>
    </section>

    <UiModal
      :open="showAssistantEditor"
      :title="assistantDraft.id ? 'Sửa Assistant' : 'Tạo Assistant mới'"
      description="Persona, voice và model được cô lập theo Assistant; thiết bị nhận cấu hình khi kết nối mới."
      size="lg"
      @close="showAssistantEditor = false"
    >
      <div class="assistant-editor">
        <div class="assistant-editor__identity">
          <div class="assistant-editor__mark"><Bot :size="21" /></div>
          <UiInput v-model="assistantDraft.name" label="Tên Assistant" placeholder="VeeTee phòng khách" autocomplete="off" />
        </div>

        <UiTextarea
          v-model="assistantDraft.base_prompt"
          label="Base prompt / Persona"
          :rows="8"
          maxlength="32768"
          placeholder="Bạn là VeeTee…"
          hint="Chỉ mô tả tính cách, vai trò và quy tắc phản hồi của Assistant này."
        />

        <div class="assistant-editor__grid">
          <UiSelect v-model="assistantDraft.voice" label="Voice" :options="voiceOptions" searchable />
          <UiSelect v-model="assistantDraft.model" label="LLM model" :options="modelOptions" searchable />
        </div>

        <UiSwitch
          v-model="assistantDraft.enabled"
          label="Cho phép thiết bị kết nối"
          description="Khi tắt, các kết nối mới bind với Assistant này sẽ bị từ chối."
        />
      </div>
      <template #footer>
        <UiButton variant="ghost" @click="showAssistantEditor = false">Hủy</UiButton>
        <UiButton variant="primary" :loading="busy" @click="submitAssistant"><template #icon><Save :size="16" /></template>Lưu Assistant</UiButton>
      </template>
    </UiModal>

    <UiModal :open="confirmState.open" :title="confirmState.title" :description="confirmState.description" size="sm" @close="confirmState.open = false">
      <div class="danger-confirm">
        <div class="danger-confirm__icon"><ShieldAlert :size="24" /></div>
        <div><strong>Thao tác có ảnh hưởng quyền truy cập</strong><p>Kiểm tra đúng đối tượng trước khi xác nhận. Backend vẫn áp dụng các ràng buộc an toàn hiện có.</p></div>
      </div>
      <template #footer>
        <UiButton variant="ghost" @click="confirmState.open = false">Hủy</UiButton>
        <UiButton variant="danger" @click="confirmDangerousAction"><template #icon><AlertTriangle :size="15" /></template>Xác nhận</UiButton>
      </template>
    </UiModal>

    <UiModal :open="authRequired" title="Đăng nhập quản trị" description="Xác thực một lần để server tạo HttpOnly session cookie. Token không được lưu trong localStorage hoặc sessionStorage." size="sm" :close-on-backdrop="false">
      <form class="login-form" @submit.prevent="login">
        <div class="login-form__hero"><div class="login-form__icon"><KeyRound :size="22" /></div><div><strong>Management access</strong><span>VEETEE_MANAGEMENT_TOKEN</span></div></div>
        <UiInput v-model="managerToken" label="Management token" type="password" autocomplete="current-password" autofocus placeholder="••••••••••••••••" :error="loginError" />
        <UiButton type="submit" variant="primary" class="w-full" :loading="loginBusy" :disabled="!managerToken.trim()">Đăng nhập</UiButton>
      </form>
    </UiModal>

    <UiToast :message="toast.text" :tone="toast.tone" />
  </div>
</template>