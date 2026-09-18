<script setup>
import { Bot, CheckCircle2, Cpu, KeyRound, Mic2, Network, RadioTower, Sparkles, ArrowUpRight, Activity } from '@lucide/vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiButton from '../components/ui/UiButton.vue'

const props = defineProps({
  health: { type: Object, default: null },
  diagnostics: { type: Object, default: null },
  assistants: { type: Array, default: () => [] },
  activeDevices: { type: Array, default: () => [] },
  onlineDevices: { type: Array, default: () => [] },
  pendingDevices: { type: Array, default: () => [] },
})
const emit = defineEmits(['navigate', 'create-assistant'])

const flow = [
  { icon: RadioTower, title: 'ESP32 yêu cầu OTA', text: 'Firmware stock nhận mã pairing 6 số.' },
  { icon: KeyRound, title: 'Manager xác nhận', text: 'Chọn đúng Assistant cho thiết bị.' },
  { icon: CheckCircle2, title: 'Cấp credential', text: 'Mỗi Device + Client có token riêng.' },
  { icon: Network, title: 'Bind Assistant', text: 'Persona, voice và model được áp dụng.' },
]
</script>

<template>
  <div class="workspace-view overview-view">
    <PageHeader eyebrow="01 / Control" title="Mission Control" description="Một màn hình để nhìn toàn bộ trạng thái VeeTee, runtime voice và các thiết bị đang hoạt động.">
      <template #actions>
        <UiButton variant="primary" @click="emit('create-assistant')"><template #icon><Sparkles :size="15" /></template>Tạo Assistant</UiButton>
      </template>
    </PageHeader>

    <section class="mission-hero">
      <div class="mission-hero__copy">
        <span class="mission-hero__eyebrow"><Activity :size="13" /> LIVE SYSTEM</span>
        <h2>{{ health?.status === 'healthy' ? 'VeeTee đang sẵn sàng.' : 'Đang kiểm tra trạng thái VeeTee.' }}</h2>
        <p>Điều phối Assistant, ESP32 và voice pipeline từ một workspace duy nhất.</p>
        <div class="mission-hero__actions">
          <button type="button" @click="emit('navigate','console')">Mở Voice Console <ArrowUpRight :size="15" /></button>
          <button type="button" @click="emit('navigate','devices')">Quản lý thiết bị</button>
        </div>
      </div>
      <div class="mission-hero__signal">
        <div class="mission-hero__signal-ring"><RadioTower :size="27" /></div>
        <strong>{{ diagnostics?.server?.active_sessions ?? health?.active_sessions ?? 0 }}</strong>
        <span>active sessions</span>
      </div>
    </section>

    <section class="metric-strip" aria-label="System metrics">
      <div class="metric-strip__item">
        <span>SERVER</span><strong>{{ health?.status || '—' }}</strong><small>Runtime health</small>
      </div>
      <div class="metric-strip__item">
        <span>ASSISTANTS</span><strong>{{ assistants.length }}</strong><small>Identity profiles</small>
      </div>
      <div class="metric-strip__item">
        <span>DEVICES</span><strong>{{ activeDevices.length }}</strong><small>{{ onlineDevices.length }} online</small>
      </div>
      <div class="metric-strip__item metric-strip__item--warning">
        <span>PENDING</span><strong>{{ pendingDevices.length }}</strong><small>Pair requests</small>
      </div>
    </section>

    <div class="mission-grid">
      <section class="mission-panel mission-panel--flow">
        <header class="mission-panel__header">
          <div><span>PAIRING ROUTE</span><h3>Luồng kết nối an toàn</h3></div>
          <small>Stock firmware compatible</small>
        </header>
        <div class="route-list">
          <div v-for="(step, index) in flow" :key="step.title" class="route-step">
            <div class="route-step__rail"><span>0{{ index + 1 }}</span><i></i></div>
            <div class="route-step__icon"><component :is="step.icon" :size="17" /></div>
            <div class="route-step__copy"><strong>{{ step.title }}</strong><p>{{ step.text }}</p></div>
          </div>
        </div>
      </section>

      <div class="mission-side">
        <section class="mission-panel runtime-board">
          <header class="mission-panel__header">
            <div><span>VOICE RUNTIME</span><h3>Pipeline hiện tại</h3></div>
            <Mic2 :size="17" />
          </header>
          <dl class="runtime-board__list">
            <div><dt>ASR</dt><dd>{{ diagnostics?.asr?.provider || '—' }}</dd></div>
            <div><dt>LLM</dt><dd>{{ diagnostics?.llm?.model || '—' }}</dd></div>
            <div><dt>TTS</dt><dd>{{ diagnostics?.tts?.voice || diagnostics?.tts?.provider || '—' }}</dd></div>
            <div><dt>Barge-in</dt><dd>{{ diagnostics?.server?.barge_in_policy || '—' }}</dd></div>
          </dl>
        </section>

        <section class="quick-stack">
          <button type="button" @click="emit('navigate','console')"><span><Mic2 :size="17" /></span><div><strong>Voice Console</strong><small>Test hội thoại realtime</small></div><ArrowUpRight :size="15" /></button>
          <button type="button" @click="emit('create-assistant')"><span><Bot :size="17" /></span><div><strong>New Assistant</strong><small>Tạo persona mới</small></div><ArrowUpRight :size="15" /></button>
          <button type="button" @click="emit('navigate','devices')"><span><Cpu :size="17" /></span><div><strong>Pair Device</strong><small>Duyệt ESP32 mới</small></div><ArrowUpRight :size="15" /></button>
          <button type="button" @click="emit('navigate','runtime')"><span><KeyRound :size="17" /></span><div><strong>Runtime</strong><small>Provider & secrets</small></div><ArrowUpRight :size="15" /></button>
        </section>
      </div>
    </div>
  </div>
</template>