<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import type { Agent, Device, LabSession } from "../../api/schemas";
import { labEventDetails, useRealtimeLab } from "../../composables/useRealtimeLab";
import { VtBadge, VtButton, VtField, VtIcon, VtMetricStrip, VtOperationsHero, VtPageHeader, VtSelect } from "../ui";

const props = defineProps<{
  agents: Agent[];
  devices: Device[];
  createSession: (input: { agentId: string; inputMode: "text" | "audio_replay" | "live_mic"; mcpMode: "simulated" | "selected_device" | "disabled"; deviceId?: string }) => Promise<LabSession>;
  toast: (message: string, tone?: "success" | "danger" | "info") => void;
}>();
const { t } = useI18n();

const text = ref("");
const chatElement = ref<HTMLElement>();
const lab = useRealtimeLab({ createSession: props.createSession, toast: props.toast });
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0));
const selectedAgent = computed(() => props.agents.find((agent) => agent.id === lab.agentId.value));
const fidelity = computed(() => [
  t(`lab.fidelity.${lab.inputMode.value}.title`),
  t(`lab.fidelity.${lab.inputMode.value}.description`),
] as const);
const labSummary = computed(() => [
  {
    label: t("lab.summary.input"),
    value: t(`lab.summary.inputModes.${lab.inputMode.value}`),
    detail: fidelity.value[0],
    tone: "info" as const,
  },
  {
    label: t("lab.summary.mcp"),
    value: t(`lab.summary.mcpModes.${lab.mcpMode.value}`),
    detail: t(lab.mcpMode.value === "selected_device" ? "lab.summary.deviceCatalog" : "lab.summary.noRealDevice"),
    tone: "neutral" as const,
  },
  {
    label: t("lab.summary.session"),
    value: t(lab.connected.value ? "lab.summary.live" : "lab.summary.waiting"),
    detail: t(lab.connected.value ? "lab.summary.tokenUsed" : "lab.summary.socketClosed"),
    tone: lab.connected.value ? "success" as const : "neutral" as const,
  },
]);

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
    <VtPageHeader :eyebrow="t('pages.lab.eyebrow')" :title="t('pages.lab.title')" :description="t('pages.lab.description')">
      <template #actions>
        <VtButton v-if="lab.connected.value" variant="secondary" @click="lab.closeSession(true)"><VtIcon name="stop" :size="16" /> {{ t("lab.actions.end") }}</VtButton>
        <VtButton v-else :busy="lab.starting.value" :disabled="!publishedAgents.length" @click="lab.startSession"><VtIcon name="play" :size="16" /> {{ t("lab.actions.start") }}</VtButton>
      </template>
    </VtPageHeader>

    <div class="lab-dashboard" data-page-section="summary">
      <VtOperationsHero
        :eyebrow="t('lab.hero.eyebrow')"
        :title="t('lab.hero.title')"
        :description="fidelity[1]"
        :value="publishedAgents.length"
        :value-label="t('lab.hero.publishedAgents')"
        :value-hint="t(lab.connected.value ? 'lab.hero.running' : publishedAgents.length ? 'lab.hero.ready' : 'lab.hero.needsAgent')"
        icon="lab"
      />
      <VtMetricStrip :items="labSummary" />
    </div>

    <div class="lab-setup-card" data-page-section="setup">
      <VtField :label="t('lab.setup.agent')" control-id="labAgent"><VtSelect v-model="lab.agentId.value" :disabled="lab.locked.value"><option value="">{{ t("lab.setup.noPublishedAgent") }}</option><option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }} · {{ agent.defaultLocale }}</option></VtSelect></VtField>
      <VtField :label="t('lab.setup.inputMode')" control-id="labInputMode"><VtSelect v-model="lab.inputMode.value" :disabled="lab.locked.value"><option value="text">{{ t("lab.setup.textMode") }}</option><option value="audio_replay">{{ t("lab.setup.replayMode") }}</option><option value="live_mic">{{ t("lab.setup.micMode") }}</option></VtSelect></VtField>
      <VtField :label="t('lab.setup.mcpMode')" control-id="labMcpMode"><VtSelect v-model="lab.mcpMode.value" :disabled="lab.locked.value"><option value="simulated">{{ t("lab.setup.simulated") }}</option><option value="selected_device">{{ t("lab.setup.selectedDevice") }}</option><option value="disabled">{{ t("lab.setup.disabled") }}</option></VtSelect></VtField>
      <VtField v-if="lab.mcpMode.value === 'selected_device'" :label="t('lab.setup.mcpDevice')" control-id="labDeviceField"><VtSelect v-model="lab.deviceId.value" :disabled="lab.locked.value"><option value="">{{ t("lab.setup.noDevice") }}</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.status }}</option></VtSelect></VtField>
      <div id="labFidelity" class="fidelity-card"><span class="vt-kicker">{{ t("lab.setup.fidelity") }}</span><b>{{ fidelity[0] }}</b><p>{{ fidelity[1] }}</p></div>
    </div>

    <div class="lab-workspace">
      <section class="lab-console">
        <header><div><VtBadge :tone="lab.stateTone.value === 'error' ? 'danger' : lab.stateTone.value === 'running' ? 'success' : 'neutral'" dot id="labState">{{ lab.state.value }}</VtBadge><span>{{ lab.sessionId.value ? lab.sessionId.value.slice(0, 12).toUpperCase() : t("lab.console.noSession") }}</span></div><small id="labPromptSnapshot">{{ lab.activePrompt.value?.applied ? t("lab.console.promptSnapshot", { version: lab.activePrompt.value.version, personality: lab.activePrompt.value.personality }) : selectedAgent ? `${selectedAgent.name} · v${selectedAgent.publishedVersion}` : t("lab.console.noAgent") }}</small></header>
        <div class="lab-stage">
          <div class="lab-stage-copy"><span class="vt-kicker">{{ t("lab.console.turnArbiter") }}</span><h2>{{ lab.state.value }}</h2><p>{{ lab.prompt.value }}</p></div>
          <div class="lab-orb" :class="[lab.stateTone.value, { listening: lab.listening.value }]"><span></span><i></i><b>V</b></div>
          <div class="lab-chat" id="labChat" ref="chatElement">
            <div v-for="message in lab.messages.value" :key="message.id" class="lab-message" :class="message.kind">{{ message.text || "…" }}</div>
          </div>
        </div>
        <div class="lab-input-zone">
          <div v-if="lab.connected.value && (!lab.audioReady.value || lab.audioError.value)" class="lab-audio-gate" role="alert">
            <VtIcon name="warning" :size="18" />
            <span><b>{{ t("lab.audio.blocked") }}</b><small>{{ lab.audioError.value || t("lab.audio.enableHint") }}</small></span>
            <VtButton size="sm" variant="secondary" @click="lab.enableAudio">{{ t("lab.audio.enable") }}</VtButton>
          </div>
          <form v-if="lab.inputMode.value === 'text'" id="labTextForm" class="lab-text-form" @submit.prevent="submit"><label class="sr-only" for="labTextInput">{{ t("lab.input.textLabel") }}</label><input id="labTextInput" v-model="text" :disabled="!lab.canSubmit.value" :placeholder="t('lab.input.textPlaceholder')" /><VtButton type="submit" size="sm" :disabled="!lab.canSubmit.value || !text.trim()">{{ t("lab.input.send") }} <VtIcon name="arrow" :size="15" /></VtButton></form>
          <div v-else-if="lab.inputMode.value === 'audio_replay'" id="labAudioReplay" class="lab-file-form"><label><input type="file" accept="audio/*" @change="chooseAudio" /><span><VtIcon name="upload" :size="18" /> {{ lab.replayFile.value?.name ?? t("lab.input.chooseAudio") }}</span></label><VtButton size="sm" :busy="lab.replayBusy.value" :disabled="!lab.canSubmit.value || !lab.replayFile.value" @click="lab.replayAudio">{{ t("lab.input.replay") }}</VtButton><small>{{ lab.replayMeta.value }}</small></div>
          <div v-else id="labLiveMic" class="lab-mic-form"><VtButton :variant="lab.micActive.value ? 'danger' : 'secondary'" :disabled="!lab.connected.value" @click="lab.toggleMicrophone"><VtIcon :name="lab.micActive.value ? 'stop' : 'mic'" :size="17" /> {{ t(lab.micActive.value ? "lab.input.disableMic" : "lab.input.enableMic") }}</VtButton><p>{{ t(lab.micActive.value ? "lab.input.micActive" : "lab.input.micRequirement") }}</p></div>
        </div>
        <footer class="lab-controls"><button id="labWakeButton" type="button" :disabled="!lab.connected.value" @click="lab.wake"><VtIcon name="mic" :size="18" /><span><b>{{ t("lab.controls.wake") }}</b><small>{{ t("lab.controls.wakeHint") }}</small></span></button><button id="interruptButton" class="interrupt" type="button" :disabled="!lab.connected.value" @click="lab.interrupt"><VtIcon name="stop" :size="18" /><span><b>{{ t("lab.controls.interrupt") }}</b><small>{{ t("lab.controls.interruptHint") }}</small></span></button></footer>
      </section>

      <aside class="lab-observability">
        <article class="vt-panel"><header class="panel-header"><div><span class="vt-kicker">{{ t("lab.observability.latencyEyebrow") }}</span><h2>{{ t("lab.observability.latency") }}</h2></div></header><div id="labMetrics" class="metric-grid"><div v-for="metric in lab.metrics.value" :key="metric.label"><span>{{ metric.label }}</span><b>{{ metric.value }}</b></div></div></article>
        <article class="vt-panel event-stream"><header class="panel-header"><div><span class="vt-kicker">{{ t("lab.observability.eventsEyebrow") }}</span><h2>{{ t("lab.observability.events") }}</h2></div><button class="text-link" type="button" @click="lab.showRaw.value = !lab.showRaw.value">{{ t(lab.showRaw.value ? "lab.observability.timeline" : "lab.observability.rawJson") }}</button></header>
          <pre v-if="lab.showRaw.value" class="raw-events">{{ lab.rawEvents.value }}</pre>
          <div v-else id="eventLog" class="lab-event-list"><div v-for="(event, index) in lab.events.value.slice(-30)" :key="`${event.elapsed_ms}-${index}`" :class="{ active: index === lab.events.value.slice(-30).length - 1, bypassed: event.event.includes('bypassed') }"><i></i><span><b>{{ event.event }}</b><small>{{ labEventDetails(event) }}</small></span><em>+{{ Math.round(event.elapsed_ms) }} ms</em></div><div v-if="!lab.events.value.length" class="lab-event-empty"><VtIcon name="telemetry" :size="24" /><b>{{ t(lab.connected.value ? "lab.empty.connectedTitle" : "lab.empty.title") }}</b><small>{{ t(lab.connected.value ? "lab.empty.connectedBody" : "lab.empty.body") }}</small></div></div>
        </article>
      </aside>
    </div>
  </section>
</template>
