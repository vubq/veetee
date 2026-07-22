<script setup lang="ts">
import { computed } from "vue";

import type { Agent, ConversationEvent, Device, Provider } from "../../api/schemas";
import type { ManagerPage } from "../../types/manager";
import { formatDate, statusTone } from "../../utils/format";
import { VtBadge, VtButton, VtEmptyState, VtIcon, VtPageHeader } from "../ui";

const props = defineProps<{
  devices: Device[];
  agents: Agent[];
  providers: Provider[];
  events: ConversationEvent[];
  ready: boolean;
}>();
const emit = defineEmits<{ navigate: [page: ManagerPage]; pair: [] }>();

const onlineDevices = computed(() => props.devices.filter((device) => device.status === "online").length);
const healthyProviders = computed(
  () => props.providers.filter((provider) => provider.enabled && provider.health === "healthy").length,
);
const activeAgent = computed(() => props.agents.find((agent) => agent.publishedVersion > 0) ?? props.agents[0]);
const recentEvents = computed(() => props.events.slice(-6).reverse());
const lastLatency = computed(() => {
  const tts = [...props.events].reverse().find((event) => event.eventType === "tts.start");
  if (!tts?.turnId) return undefined;
  const stt = [...props.events]
    .reverse()
    .find((event) => event.turnId === tts.turnId && event.eventType === "stt.final");
  if (!stt) return undefined;
  return Math.max(0, new Date(tts.occurredAt).getTime() - new Date(stt.occurredAt).getTime());
});

const pipeline = [
  ["VAD", "Silero VAD", "Phát hiện giọng nói"],
  ["ASR", "Zipformer VI 30M INT8", "Nhận dạng nhanh tại máy"],
  ["LLM", "9Router", "OpenAI-compatible routing"],
  ["TTS", "VieNeu · Trúc Ly", "Local voice · tốc độ 1.2×"],
];
</script>

<template>
  <section class="vt-page" data-page="overview">
    <VtPageHeader
      eyebrow="CONTROL ROOM / TỔNG QUAN"
      title="Robot đang vận hành thế nào?"
      description="Một màn hình duy nhất để nhìn trạng thái thiết bị, pipeline AI và những lượt hội thoại gần nhất."
    >
      <template #actions>
        <VtButton variant="secondary" @click="emit('navigate', 'lab')"><VtIcon name="lab" :size="18" /> Mở Realtime Lab</VtButton>
        <VtButton @click="emit('pair')"><VtIcon name="plus" :size="18" /> Ghép thiết bị</VtButton>
      </template>
    </VtPageHeader>

    <div class="overview-hero">
      <div class="overview-hero-copy">
        <VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ ready ? "Hệ thống sẵn sàng" : "Manager API gián đoạn" }}</VtBadge>
        <h2>Veetee nghe tự nhiên,<br /><em>phản hồi tức thời.</em></h2>
        <p>TurnArbiter quản lý wake, nghe, admission, xử lý, MCP, nói và cancellation trong một luồng có thể quan sát.</p>
        <button class="text-link on-dark" type="button" @click="emit('navigate', 'devices')">Mở workspace thiết bị <VtIcon name="arrow" :size="16" /></button>
      </div>
      <div class="voice-orbit" :class="{ 'is-live': onlineDevices > 0 }" aria-hidden="true">
        <span class="orbit-ring ring-one"></span><span class="orbit-ring ring-two"></span>
        <div class="orbit-core"><i></i><b>V</b></div>
        <small>{{ onlineDevices > 0 ? "VOICE READY" : "STANDBY" }}</small>
      </div>
    </div>

    <div class="stat-grid">
      <article class="stat-card"><span>Thiết bị online</span><strong>{{ onlineDevices }}<small>/{{ devices.length }}</small></strong><p>{{ devices.length ? "Đồng bộ qua WebSocket" : "Chưa ghép thiết bị" }}</p></article>
      <article class="stat-card"><span>Provider khỏe</span><strong>{{ healthyProviders }}<small>/{{ providers.filter((item) => item.enabled).length }}</small></strong><p>Được kiểm tra theo adapter</p></article>
      <article class="stat-card"><span>Agent đã publish</span><strong>{{ agents.filter((item) => item.publishedVersion > 0).length }}</strong><p>Version bất biến, có thể rollout</p></article>
      <article class="stat-card latency-row"><span>ASR → TTS gần nhất</span><strong>{{ lastLatency ?? "—" }}<small v-if="lastLatency !== undefined"> ms</small></strong><p>Không tính VAD và thời lượng người nói</p></article>
    </div>

    <div class="content-grid is-wide-left">
      <article class="vt-panel pipeline-panel">
        <header class="panel-header"><div><span class="vt-kicker">LOCAL VOICE STACK</span><h2>Pipeline đang dùng</h2></div><VtBadge tone="info">LAN-FIRST</VtBadge></header>
        <div class="pipeline-list">
          <div v-for="(step, index) in pipeline" :key="step[0]" class="pipeline-step">
            <span>{{ String(index + 1).padStart(2, "0") }}</span><div><small>{{ step[0] }}</small><b>{{ step[1] }}</b><p>{{ step[2] }}</p></div><i :class="{ active: ready }"></i>
          </div>
        </div>
      </article>

      <article class="vt-panel agent-spotlight">
        <header class="panel-header"><div><span class="vt-kicker">PUBLISHED ASSISTANT</span><h2>Trợ lý hiện tại</h2></div></header>
        <template v-if="activeAgent">
          <div class="agent-monogram">{{ activeAgent.name.slice(0, 1).toUpperCase() }}</div>
          <h3>{{ activeAgent.name }}</h3><p>{{ activeAgent.persona }}</p>
          <div class="agent-meta"><span>{{ activeAgent.defaultLocale }}</span><span>{{ activeAgent.interactionMode }}</span><span>v{{ activeAgent.publishedVersion }}</span></div>
          <VtButton variant="secondary" @click="emit('navigate', 'agents')">Cấu hình agent <VtIcon name="arrow" :size="16" /></VtButton>
        </template>
        <VtEmptyState v-else icon="agent" title="Chưa có agent" text="Tạo và publish trợ lý đầu tiên để bắt đầu hội thoại." />
      </article>
    </div>

    <div class="content-grid">
      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">FLEET</span><h2>Thiết bị</h2></div><button class="text-link" type="button" @click="emit('navigate', 'devices')">Xem tất cả <VtIcon name="arrow" :size="15" /></button></header>
        <div v-if="devices.length" class="compact-list">
          <button v-for="device in devices.slice(0, 4)" :key="device.id" type="button" @click="emit('navigate', 'devices')">
            <span class="list-icon"><VtIcon name="device" :size="20" /></span><span><b>{{ device.name }}</b><small>{{ device.hardwareId }} · FW {{ device.firmwareVersion ?? "—" }}</small></span><VtBadge :tone="statusTone(device.status)" dot>{{ device.status }}</VtBadge>
          </button>
        </div>
        <VtEmptyState v-else icon="device" title="Chưa có robot" text="Nhập mã 6 số đang hiển thị trên ESP32 để ghép thiết bị."><VtButton size="sm" @click="emit('pair')">Ghép ngay</VtButton></VtEmptyState>
      </article>

      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">RECENT TRACE</span><h2>Sự kiện gần nhất</h2></div><button class="text-link" type="button" @click="emit('navigate', 'devices')">Mở trong thiết bị <VtIcon name="arrow" :size="15" /></button></header>
        <div v-if="recentEvents.length" class="event-list compact">
          <div v-for="event in recentEvents" :key="event.id"><i></i><span><b>{{ event.eventType }}</b><small>{{ event.sessionId.slice(0, 12) }} · gen {{ event.generation }}</small></span><time>{{ formatDate(event.occurredAt) }}</time></div>
        </div>
        <VtEmptyState v-else icon="telemetry" title="Chưa có sự kiện" text="Khi robot bắt đầu hội thoại, trace sẽ xuất hiện tại đây." />
      </article>
    </div>
  </section>
</template>
