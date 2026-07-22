<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import type { Agent, Device, LabSession } from "../../api/schemas";
import { labEventDetails, useRealtimeLab } from "../../composables/useRealtimeLab";
import { VtBadge, VtButton, VtField, VtIcon, VtPageHeader, VtSelect } from "../ui";

const props = defineProps<{
  agents: Agent[];
  devices: Device[];
  createSession: (input: { agentId: string; inputMode: "text" | "audio_replay" | "live_mic"; mcpMode: "simulated" | "selected_device" | "disabled"; deviceId?: string }) => Promise<LabSession>;
  toast: (message: string, tone?: "success" | "danger" | "info") => void;
}>();

const text = ref("");
const chatElement = ref<HTMLElement>();
const lab = useRealtimeLab({ createSession: props.createSession, toast: props.toast });
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0));
const selectedAgent = computed(() => props.agents.find((agent) => agent.id === lab.agentId.value));
const fidelity = computed(() => ({
  text: ["Admission · LLM · TTS thật", "Text không giả VAD/ASR; hai bước này được đánh dấu BYPASS."],
  audio_replay: ["VAD · ASR · LLM · TTS thật", "Browser decode và gửi PCM16 realtime; chưa đo Opus/AEC của ESP32."],
  live_mic: ["Live voice pipeline", "Dùng AEC/NS của browser; không thay thế kiểm thử microphone và loa vật lý."],
})[lab.inputMode.value]);

watch(
  () => [publishedAgents.value, props.devices] as const,
  () => {
    if (!publishedAgents.value.some((agent) => agent.id === lab.agentId.value)) lab.agentId.value = publishedAgents.value[0]?.id ?? "";
    if (!props.devices.some((device) => device.id === lab.deviceId.value)) lab.deviceId.value = props.devices[0]?.id ?? "";
  },
  { immediate: true, deep: true },
);

watch(
  () => lab.messages.value.map((message) => message.text).join(""),
  async () => { await nextTick(); if (chatElement.value) chatElement.value.scrollTop = chatElement.value.scrollHeight; },
);

function submit(): void {
  if (lab.submitText(text.value)) text.value = "";
}

function chooseAudio(event: Event): void {
  lab.replayFile.value = (event.target as HTMLInputElement).files?.[0];
}
</script>

<template>
  <section class="vt-page" data-page="lab">
    <VtPageHeader eyebrow="VOICE PIPELINE / REALTIME LAB" title="Nói chuyện với pipeline thật" description="Mô phỏng sát thiết bị để đo admission, LLM, MCP, TTS và cancellation. Mỗi input mode luôn ghi rõ phần nào thật, phần nào bypass." >
      <template #actions>
        <VtButton v-if="lab.connected.value" variant="secondary" @click="lab.closeSession(true)"><VtIcon name="stop" :size="16" /> Kết thúc phiên</VtButton>
        <VtButton v-else :busy="lab.starting.value" :disabled="!publishedAgents.length" @click="lab.startSession"><VtIcon name="play" :size="16" /> Bắt đầu phiên thử</VtButton>
      </template>
    </VtPageHeader>

    <div class="lab-setup-card">
      <VtField label="Trợ lý"><VtSelect id="labAgent" v-model="lab.agentId.value" :disabled="lab.locked.value"><option value="">Chưa có agent đã publish</option><option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }} · {{ agent.defaultLocale }}</option></VtSelect></VtField>
      <VtField label="Input mode"><VtSelect id="labInputMode" v-model="lab.inputMode.value" :disabled="lab.locked.value"><option value="text">Text · bỏ qua VAD/ASR</option><option value="audio_replay">Audio Replay · pipeline speech thật</option><option value="live_mic">Live Mic · pipeline speech thật</option></VtSelect></VtField>
      <VtField label="MCP mode"><VtSelect id="labMcpMode" v-model="lab.mcpMode.value" :disabled="lab.locked.value"><option value="simulated">Simulated tools</option><option value="selected_device">Selected device</option><option value="disabled">Disabled</option></VtSelect></VtField>
      <VtField v-if="lab.mcpMode.value === 'selected_device'" id="labDeviceField" label="Thiết bị MCP"><VtSelect v-model="lab.deviceId.value" :disabled="lab.locked.value"><option value="">Chưa có thiết bị</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.status }}</option></VtSelect></VtField>
      <div id="labFidelity" class="fidelity-card"><span class="vt-kicker">FIDELITY</span><b>{{ fidelity[0] }}</b><p>{{ fidelity[1] }}</p></div>
    </div>

    <div class="lab-workspace">
      <section class="lab-console">
        <header><div><VtBadge :tone="lab.stateTone.value === 'error' ? 'danger' : lab.stateTone.value === 'running' ? 'success' : 'neutral'" dot id="labState">{{ lab.state.value }}</VtBadge><span>{{ lab.sessionId.value ? lab.sessionId.value.slice(0, 12).toUpperCase() : "NO SESSION" }}</span></div><small>{{ selectedAgent ? `${selectedAgent.name} · v${selectedAgent.publishedVersion}` : "Chưa chọn agent" }}</small></header>
        <div class="lab-stage">
          <div class="lab-stage-copy"><span class="vt-kicker">TURNARBITER</span><h2>{{ lab.state.value }}</h2><p>{{ lab.prompt.value }}</p></div>
          <div class="lab-orb" :class="[lab.stateTone.value, { listening: lab.listening.value }]"><span></span><i></i><b>V</b></div>
          <div class="lab-chat" id="labChat" ref="chatElement">
            <div v-for="message in lab.messages.value" :key="message.id" class="lab-message" :class="message.kind">{{ message.text || "…" }}</div>
          </div>
        </div>
        <div class="lab-input-zone">
          <form v-if="lab.inputMode.value === 'text'" id="labTextForm" class="lab-text-form" @submit.prevent="submit"><input id="labTextInput" v-model="text" :disabled="!lab.canSubmit.value" placeholder="Nhập nội dung tương đương một lượt nói…" /><VtButton type="submit" size="sm" :disabled="!lab.canSubmit.value || !text.trim()">Gửi lượt nói <VtIcon name="arrow" :size="15" /></VtButton></form>
          <div v-else-if="lab.inputMode.value === 'audio_replay'" id="labAudioReplay" class="lab-file-form"><label><input type="file" accept="audio/*" @change="chooseAudio" /><span><VtIcon name="upload" :size="18" /> {{ lab.replayFile.value?.name ?? "Chọn audio tối đa 20 giây" }}</span></label><VtButton size="sm" :busy="lab.replayBusy.value" :disabled="!lab.canSubmit.value || !lab.replayFile.value" @click="lab.replayAudio">Replay realtime</VtButton><small>{{ lab.replayMeta.value }}</small></div>
          <div v-else id="labLiveMic" class="lab-mic-form"><VtButton :variant="lab.micActive.value ? 'danger' : 'secondary'" :disabled="!lab.connected.value" @click="lab.toggleMicrophone"><VtIcon :name="lab.micActive.value ? 'stop' : 'mic'" :size="17" /> {{ lab.micActive.value ? "Tắt microphone" : "Bật microphone" }}</VtButton><p>{{ lab.micActive.value ? "Mic đang mở · browser AEC/NS bật." : "Live Mic cần HTTPS hoặc localhost." }}</p></div>
        </div>
        <footer class="lab-controls"><button id="labWakeButton" type="button" :disabled="!lab.connected.value" @click="lab.wake"><VtIcon name="mic" :size="18" /><span><b>Wake</b><small>Mở gate khi assistant ngủ</small></span></button><button id="interruptButton" class="interrupt" type="button" :disabled="!lab.connected.value" @click="lab.interrupt"><VtIcon name="stop" :size="18" /><span><b>Ngắt AI</b><small>Cancel generation, nghe tiếp</small></span></button></footer>
      </section>

      <aside class="lab-observability">
        <article class="vt-panel"><header class="panel-header"><div><span class="vt-kicker">TURN LATENCY</span><h2>Độ trễ</h2></div></header><div id="labMetrics" class="metric-grid"><div v-for="metric in lab.metrics.value" :key="metric.label"><span>{{ metric.label }}</span><b>{{ metric.value }}</b></div></div></article>
        <article class="vt-panel event-stream"><header class="panel-header"><div><span class="vt-kicker">LIVE EVENTS</span><h2>Event stream</h2></div><button class="text-link" type="button" @click="lab.showRaw.value = !lab.showRaw.value">{{ lab.showRaw.value ? "Timeline" : "Raw JSON" }}</button></header>
          <pre v-if="lab.showRaw.value" class="raw-events">{{ lab.rawEvents.value }}</pre>
          <div v-else id="eventLog" class="lab-event-list"><div v-for="(event, index) in lab.events.value.slice(-30)" :key="`${event.elapsed_ms}-${index}`" :class="{ active: index === lab.events.value.slice(-30).length - 1, bypassed: event.event.includes('bypassed') }"><i></i><span><b>{{ event.event }}</b><small>{{ labEventDetails(event) }}</small></span><em>+{{ Math.round(event.elapsed_ms) }} ms</em></div><div v-if="!lab.events.value.length" class="lab-event-empty"><VtIcon name="telemetry" :size="24" /><b>{{ lab.connected.value ? "Đã mở phiên" : "Chưa có phiên đang chạy" }}</b><small>{{ lab.connected.value ? "Gửi text, audio hoặc nói để tạo turn." : "Token một lần chỉ cấp khi bạn bấm bắt đầu." }}</small></div></div>
        </article>
      </aside>
    </div>
  </section>
</template>
