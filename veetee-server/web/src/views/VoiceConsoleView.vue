<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  Activity, Braces, CircleStop, Eraser, HeartPulse, Mic, PlugZap, Radio, RefreshCw,
  Send, Speaker, TerminalSquare, Unplug, Waves, Wifi, Zap, Power, FlaskConical,
} from '@lucide/vue'
import ChatBubble from '../components/console/ChatBubble.vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiEmptyState from '../components/ui/UiEmptyState.vue'
import UiInput from '../components/ui/UiInput.vue'
import UiSegmented from '../components/ui/UiSegmented.vue'
import UiSelect from '../components/ui/UiSelect.vue'
import UiSwitch from '../components/ui/UiSwitch.vue'
import UiTextarea from '../components/ui/UiTextarea.vue'

const props = defineProps({
  chatInput: { type: String, default: '' },
  messages: { type: Array, default: () => [] },
  protocolLog: { type: Array, default: () => [] },
  wsState: { type: String, default: 'disconnected' },
  protocolVersion: { type: Number, default: 1 },
  listenMode: { type: String, default: 'auto' },
  rawProtocol: { type: String, default: '' },
  useRealAudio: Boolean,
  isMicRecording: Boolean,
  isSyntheticAudioRunning: Boolean,
  audioStatus: { type: String, default: '' },
  firstAudioLatency: { type: Number, default: null },
  currentEmotion: { type: String, default: 'neutral' },
  pipelineVad: { type: String, default: 'Chờ audio' },
  pipelineAsr: { type: String, default: 'Chờ audio' },
  pipelineLlm: { type: String, default: 'Chờ transcript' },
  pipelineTts: { type: String, default: 'Chờ LLM' },
  health: { type: Object, default: null },
})

const emit = defineEmits([
  'update:chatInput', 'update:protocol-version', 'update:listen-mode', 'update:raw-protocol',
  'update:use-real-audio', 'connect', 'reconnect', 'unlock-audio', 'send', 'toggle-mic', 'abort', 'direct-tts',
  'health', 'ota', 'ping', 'listen-start', 'listen-stop', 'wake-detect', 'wake-start',
  'end-intent', 'send-raw', 'clear', 'runtime',
])

const chatBox = ref(null)
const showAdvanced = ref(false)
const connected = computed(() => props.wsState === 'connected')
const llmUnavailable = computed(() => (props.health?.degraded_reasons || []).includes('llm_not_warm'))
const audioNeedsUnlock = computed(() => /chặn|suspended|Lỗi phát TTS|Lỗi audio/i.test(props.audioStatus || ''))
const canSend = computed(() => connected.value && !llmUnavailable.value && !!props.chatInput.trim())
function sendIfReady() {
  if (canSend.value) emit('send')
}
const protocolOptions = [
  { value: 1, label: 'V1' },
  { value: 2, label: 'V2' },
  { value: 3, label: 'V3' },
]
const listenModeOptions = [
  { value: 'auto', label: 'Auto', description: 'Server tự quản lý chu kỳ nghe' },
  { value: 'realtime', label: 'Realtime', description: 'Giữ phiên nghe liên tục' },
  { value: 'manual', label: 'Manual', description: 'Client điều khiển start / stop' },
]
const stages = computed(() => [
  { key: 'VAD', value: props.pipelineVad, tone: /Có giọng|Kết thúc|Bỏ qua/.test(props.pipelineVad) ? 'success' : 'neutral' },
  { key: 'ASR', value: props.pipelineAsr, tone: props.pipelineAsr.startsWith('✓') || props.pipelineAsr.includes('Bỏ qua') ? 'success' : 'neutral' },
  { key: 'LLM', value: props.pipelineLlm, tone: /Đã|Bắt đầu/.test(props.pipelineLlm) ? 'success' : 'neutral' },
  { key: 'TTS', value: props.pipelineTts, tone: /Hoàn tất|Audio đầu/.test(props.pipelineTts) ? 'success' : 'neutral' },
])

watch(() => props.messages, async () => {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}, { deep: true })
</script>

<template>
  <div class="workspace-view voice-view">
    <PageHeader eyebrow="02 / Lab" title="Voice Console" description="Realtime workspace để test WebSocket, microphone, ASR, LLM, TTS và protocol diagnostics.">
      <template #actions>
        <UiButton v-if="audioNeedsUnlock" variant="secondary" size="sm" @click="emit('unlock-audio')">
          <template #icon><Speaker :size="14" /></template>
          Bật âm thanh
        </UiButton>
        <UiBadge :tone="connected ? 'success' : wsState === 'error' ? 'danger' : 'neutral'" dot>{{ wsState }}</UiBadge>
        <UiButton :variant="connected ? 'secondary' : 'primary'" @click="emit('connect')">
          <template #icon><component :is="connected ? Unplug : PlugZap" :size="15" /></template>
          {{ connected ? 'Ngắt kết nối' : 'Kết nối WebSocket' }}
        </UiButton>
      </template>
    </PageHeader>

    <section v-if="llmUnavailable" class="voice-readiness-alert">
      <div>
        <strong>LLM chưa sẵn sàng</strong>
        <p>Voice Console vẫn kết nối được để test WebSocket, ASR và TTS, nhưng AI chat sẽ không trả lời cho tới khi có GROQ_API_KEY hợp lệ.</p>
      </div>
      <UiButton variant="secondary" size="sm" @click="emit('runtime')">Mở Runtime</UiButton>
    </section>

    <section class="voice-status-strip">
      <div><span>CONNECTION</span><strong>{{ connected ? 'LIVE' : 'OFFLINE' }}</strong></div>
      <div><span>TTFA</span><strong>{{ firstAudioLatency ? `${firstAudioLatency} ms` : '—' }}</strong></div>
      <div><span>EMOTION</span><strong>{{ currentEmotion }}</strong></div>
      <div><span>AUDIO</span><strong>{{ audioStatus || 'Idle' }}</strong></div>
    </section>

    <div class="voice-lab">
      <section class="voice-session">
        <header class="voice-session__head">
          <div>
            <span>CONVERSATION STREAM</span>
            <h2>Realtime session</h2>
          </div>
          <div class="voice-session__live" :class="{ 'voice-session__live--on': connected }"><i></i>{{ connected ? 'Live' : 'Offline' }}</div>
        </header>

        <div ref="chatBox" class="voice-session__messages">
          <UiEmptyState v-if="!messages.length" title="Phiên hội thoại trống" description="Kết nối WebSocket rồi gửi text hoặc microphone để bắt đầu test pipeline.">
            <template #icon><Waves :size="25" /></template>
          </UiEmptyState>
          <ChatBubble v-for="(message, index) in messages" v-else :key="index" :role="message.role" :text="message.text" />
        </div>

        <footer class="voice-compose">
          <div class="voice-compose__main">
            <UiInput
              :model-value="chatInput"
              placeholder="Nhập nội dung test, ví dụ: Hôm nay ngày mấy?"
              :disabled="!connected || isSyntheticAudioRunning"
              @update:model-value="emit('update:chatInput', $event)"
              @keydown.enter.prevent="sendIfReady"
            />
            <UiButton variant="primary" icon-only :loading="isSyntheticAudioRunning" :disabled="!canSend" aria-label="Gửi" @click="sendIfReady">
              <template #icon><Send :size="17" /></template>
            </UiButton>
          </div>

          <UiSwitch
            :model-value="useRealAudio"
            label="Text → audio → ASR thật"
            description="VieNeu tạo audio từ text rồi stream qua VAD / ASR như input microphone."
            :disabled="!connected || isMicRecording || isSyntheticAudioRunning || llmUnavailable"
            @update:model-value="emit('update:use-real-audio', $event)"
          />

          <div class="voice-compose__tools">
            <div>
              <UiButton :variant="isMicRecording ? 'danger' : 'secondary'" size="sm" :disabled="!connected || isSyntheticAudioRunning" @click="emit('toggle-mic')">
                <template #icon><component :is="isMicRecording ? CircleStop : Mic" :size="14" /></template>
                {{ isMicRecording ? 'Dừng mic' : 'Bật mic' }}
              </UiButton>
              <UiButton variant="secondary" size="sm" @click="emit('direct-tts')"><template #icon><Speaker :size="14" /></template>TTS trực tiếp</UiButton>
              <UiButton variant="danger-ghost" size="sm" :disabled="!connected" @click="emit('abort')"><template #icon><CircleStop :size="14" /></template>Ngắt lượt</UiButton>
            </div>
            <UiButton variant="ghost" size="sm" @click="emit('clear')"><template #icon><Eraser :size="14" /></template>Xóa phiên</UiButton>
          </div>
        </footer>
      </section>

      <aside class="voice-tools">
        <section class="tool-panel">
          <header><div><span>PIPELINE</span><h3>VAD → ASR → LLM → TTS</h3></div><Zap :size="16" /></header>
          <div class="pipeline-stack">
            <div v-for="stage in stages" :key="stage.key" class="pipeline-node" :class="{ 'pipeline-node--done': stage.tone === 'success' }">
              <span>{{ stage.key }}</span><strong>{{ stage.value }}</strong><i></i>
            </div>
          </div>
        </section>

        <section class="tool-panel">
          <header><div><span>WIRE PROTOCOL</span><h3>Quick diagnostics</h3></div><TerminalSquare :size="16" /></header>

          <div class="protocol-settings">
            <div>
              <span class="ui-field__label">Protocol</span>
              <UiSegmented :model-value="protocolVersion" :options="protocolOptions" :disabled="connected" @update:model-value="emit('update:protocol-version', $event)" />
            </div>
            <UiSelect :model-value="listenMode" label="Listen mode" :options="listenModeOptions" @update:model-value="emit('update:listen-mode', $event)" />
          </div>

          <div class="protocol-actions">
            <UiButton variant="secondary" size="sm" @click="emit('health')"><template #icon><Activity :size="13" /></template>Health</UiButton>
            <UiButton variant="secondary" size="sm" @click="emit('ota')"><template #icon><Wifi :size="13" /></template>OTA</UiButton>
            <UiButton variant="secondary" size="sm" :disabled="!connected" @click="emit('ping')"><template #icon><Radio :size="13" /></template>Ping</UiButton>
            <UiButton variant="secondary" size="sm" @click="emit('reconnect')"><template #icon><RefreshCw :size="13" /></template>Reconnect</UiButton>
            <UiButton variant="secondary" size="sm" :disabled="!connected" @click="emit('listen-start')">Listen Start</UiButton>
            <UiButton variant="secondary" size="sm" :disabled="!connected" @click="emit('listen-stop')">Listen Stop</UiButton>
          </div>

          <UiButton class="protocol-advanced-toggle" variant="ghost" size="sm" @click="showAdvanced = !showAdvanced">
            <template #icon><FlaskConical :size="13" /></template>{{ showAdvanced ? 'Đóng protocol lab' : 'Mở protocol lab' }}
          </UiButton>
        </section>

        <section v-if="showAdvanced" class="tool-panel tool-panel--advanced">
          <header><div><span>ADVANCED</span><h3>Protocol lab</h3></div><Braces :size="16" /></header>
          <p>Wake/End dùng nội dung đang có trong ô chat bên trái.</p>
          <div class="protocol-actions protocol-actions--advanced">
            <UiButton variant="secondary" size="sm" :disabled="!connected || !chatInput.trim()" @click="emit('wake-detect')">Wake Detect</UiButton>
            <UiButton variant="secondary" size="sm" :disabled="!connected || !chatInput.trim()" @click="emit('wake-start')">Wake + Start</UiButton>
            <UiButton variant="secondary" size="sm" :disabled="!connected || !chatInput.trim()" @click="emit('end-intent')"><template #icon><Power :size="13" /></template>End intent</UiButton>
          </div>
          <UiTextarea :model-value="rawProtocol" label="Raw protocol JSON" :rows="4" class="console-raw-json" @update:model-value="emit('update:raw-protocol', $event)" />
          <UiButton variant="secondary" size="sm" :disabled="!connected || !rawProtocol.trim()" @click="emit('send-raw')">Send raw JSON</UiButton>
        </section>

        <section class="tool-panel tool-panel--log">
          <header><div><span>EVENT LOG</span><h3>Realtime events</h3></div><TerminalSquare :size="16" /></header>
          <pre>{{ protocolLog.length ? protocolLog.join('\n') : 'Protocol log sẵn sàng.' }}</pre>
        </section>
      </aside>
    </div>
  </div>
</template>