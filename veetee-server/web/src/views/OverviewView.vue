<script setup>
import { computed } from 'vue'
import {
  Activity,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  Cpu,
  KeyRound,
  Mic2,
  Network,
  RadioTower,
  Server,
  Sparkles,
} from '@lucide/vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiBadge from '../components/ui/UiBadge.vue'
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
  { icon: RadioTower, title: 'ESP32 yêu cầu OTA', text: 'Firmware nhận mã pairing 6 số từ server.' },
  { icon: KeyRound, title: 'Xác nhận thiết bị', text: 'Chọn Assistant và owner phù hợp cho thiết bị.' },
  { icon: CheckCircle2, title: 'Cấp credential', text: 'Mỗi Device + Client nhận credential riêng.' },
  { icon: Network, title: 'Bind Assistant', text: 'Persona, voice và model được áp dụng khi kết nối.' },
]

const readiness = computed(() => props.health?.readiness || props.health?.status || 'checking')
const readinessTone = computed(() => readiness.value === 'ready' ? 'success' : readiness.value === 'degraded' ? 'warning' : 'neutral')
</script>

<template>
  <div class="workspace-view overview-view">
    <PageHeader
      eyebrow="Tổng quan"
      title="VeeTee Control Center"
      description="Theo dõi Assistant, thiết bị và voice runtime trong cùng một workspace."
    >
      <template #actions>
        <UiButton variant="secondary" @click="emit('navigate', 'console')">
          <template #icon><Mic2 :size="16" /></template>
          Voice Console
        </UiButton>
        <UiButton variant="primary" @click="emit('create-assistant')">
          <template #icon><Sparkles :size="16" /></template>
          Tạo Assistant
        </UiButton>
      </template>
    </PageHeader>

    <section class="overview-stats" aria-label="System metrics">
      <article class="overview-stat">
        <span class="overview-stat__icon"><Server :size="18" /></span>
        <div>
          <span>Server</span>
          <strong>{{ readiness }}</strong>
          <small>Runtime readiness</small>
        </div>
        <UiBadge :tone="readinessTone" dot>{{ readiness }}</UiBadge>
      </article>

      <article class="overview-stat">
        <span class="overview-stat__icon"><Bot :size="18" /></span>
        <div>
          <span>Assistants</span>
          <strong>{{ assistants.length }}</strong>
          <small>Identity profiles</small>
        </div>
      </article>

      <article class="overview-stat">
        <span class="overview-stat__icon"><Cpu :size="18" /></span>
        <div>
          <span>Devices</span>
          <strong>{{ activeDevices.length }}</strong>
          <small>{{ onlineDevices.length }} online</small>
        </div>
      </article>

      <article class="overview-stat">
        <span class="overview-stat__icon"><Activity :size="18" /></span>
        <div>
          <span>Pending pairing</span>
          <strong>{{ pendingDevices.length }}</strong>
          <small>Requests waiting</small>
        </div>
      </article>
    </section>

    <div class="overview-layout">
      <section class="section-card overview-pairing">
        <header class="section-card__header">
          <div>
            <h2>Luồng kết nối thiết bị</h2>
            <p>Pairing an toàn, tương thích stock firmware Xiaozhi.</p>
          </div>
          <UiBadge tone="neutral">4 bước</UiBadge>
        </header>

        <div class="pairing-steps">
          <div v-for="(step, index) in flow" :key="step.title" class="pairing-step">
            <span class="pairing-step__number">{{ index + 1 }}</span>
            <span class="pairing-step__icon"><component :is="step.icon" :size="18" /></span>
            <div>
              <strong>{{ step.title }}</strong>
              <p>{{ step.text }}</p>
            </div>
          </div>
        </div>
      </section>

      <div class="overview-side">
        <section class="section-card runtime-summary">
          <header class="section-card__header">
            <div>
              <h2>Voice runtime</h2>
              <p>Cấu hình pipeline đang được server sử dụng.</p>
            </div>
            <Mic2 :size="18" />
          </header>

          <dl class="runtime-summary__list">
            <div><dt>ASR</dt><dd>{{ diagnostics?.asr?.provider || 'Chưa sẵn sàng' }}</dd></div>
            <div><dt>LLM</dt><dd>{{ diagnostics?.llm?.model || 'Chưa sẵn sàng' }}</dd></div>
            <div><dt>TTS</dt><dd>{{ diagnostics?.tts?.voice || diagnostics?.tts?.provider || 'Chưa sẵn sàng' }}</dd></div>
            <div><dt>Barge-in</dt><dd>{{ diagnostics?.server?.barge_in_policy || '—' }}</dd></div>
            <div><dt>Sessions</dt><dd>{{ diagnostics?.server?.active_sessions ?? health?.active_sessions ?? 0 }}</dd></div>
          </dl>
        </section>

        <section class="section-card quick-actions-card">
          <header class="section-card__header">
            <div>
              <h2>Thao tác nhanh</h2>
              <p>Mở thẳng khu vực cần quản lý.</p>
            </div>
          </header>

          <div class="quick-actions-list">
            <button type="button" @click="emit('navigate','console')">
              <span><Mic2 :size="17" /></span>
              <div><strong>Voice Console</strong><small>Kiểm thử hội thoại realtime</small></div>
              <ArrowUpRight :size="16" />
            </button>
            <button type="button" @click="emit('create-assistant')">
              <span><Bot :size="17" /></span>
              <div><strong>Tạo Assistant</strong><small>Persona, voice và model riêng</small></div>
              <ArrowUpRight :size="16" />
            </button>
            <button type="button" @click="emit('navigate','devices')">
              <span><Cpu :size="17" /></span>
              <div><strong>Pair thiết bị</strong><small>Duyệt ESP32 mới</small></div>
              <ArrowUpRight :size="16" />
            </button>
            <button type="button" @click="emit('navigate','runtime')">
              <span><KeyRound :size="17" /></span>
              <div><strong>Runtime</strong><small>Provider và credential</small></div>
              <ArrowUpRight :size="16" />
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
