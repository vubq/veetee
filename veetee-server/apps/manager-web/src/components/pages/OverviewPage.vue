<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";

import type { Agent, ConversationEvent, Device, Provider } from "../../api/schemas";
import type { ManagerPage } from "../../types/manager";
import { devicePresence } from "../../utils/device-presence";
import { formatDate } from "../../utils/format";
import { VtBadge, VtButton, VtEmptyState, VtIcon, VtMetricStrip, VtOperationsHero, VtPageHeader } from "../ui";

const props = defineProps<{
  devices: Device[];
  agents: Agent[];
  providers: Provider[];
  events: ConversationEvent[];
  ready: boolean;
}>();
const emit = defineEmits<{ navigate: [page: ManagerPage]; pair: [] }>();
const { t } = useI18n();

const onlineDevices = computed(() => props.devices.filter((device) => devicePresence(device).state === "online").length);
const enabledProviders = computed(() => props.providers.filter((provider) => provider.enabled).length);
const healthyProviders = computed(
  () => props.providers.filter((provider) => provider.enabled && provider.health === "healthy").length,
);
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0).length);
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

const overviewMetrics = computed(() => [
  {
    label: t("overview.metrics.onlineDevices"),
    value: `${onlineDevices.value}/${props.devices.length}`,
    detail: t(props.devices.length ? "overview.metrics.lastContact" : "overview.metrics.noPairedDevices"),
    tone: onlineDevices.value ? "success" as const : "neutral" as const,
  },
  {
    label: t("overview.metrics.healthyProviders"),
    value: `${healthyProviders.value}/${enabledProviders.value}`,
    detail: t("overview.metrics.checkedByAdapter"),
    tone: healthyProviders.value === enabledProviders.value && enabledProviders.value
      ? "success" as const
      : "warning" as const,
  },
  {
    label: t("overview.metrics.publishedAgents"),
    value: publishedAgents.value,
    detail: t("overview.metrics.immutableReady"),
    tone: publishedAgents.value ? "info" as const : "warning" as const,
  },
  {
    label: t("overview.metrics.latestLatency"),
    value: lastLatency.value === undefined ? "—" : `${lastLatency.value} ms`,
    detail: t("overview.metrics.latencyScope"),
    tone: "neutral" as const,
  },
]);

const pipeline = computed(() => [
  ["VAD", "overview.pipeline.vad", "vad"],
  ["ASR", "overview.pipeline.asr", "asr"],
  ["LLM", "overview.pipeline.llm", "llm"],
  ["TTS", "overview.pipeline.tts", "tts"],
].map(([kind, fallbackKey, providerKind]) => {
  const provider = [...props.providers]
    .filter((item) => item.kind === providerKind && item.enabled)
    .sort((left, right) => left.priority - right.priority)[0];
  return {
    kind,
    title: provider ? `${provider.adapter} · ${provider.model}` : t("overview.pipeline.notConfigured"),
    detail: provider ? `${provider.health}` : t(fallbackKey!),
    configured: Boolean(provider),
  };
}));
</script>

<template>
  <section class="vt-page" data-page="overview">
    <VtPageHeader
      :eyebrow="t('pages.overview.eyebrow')"
      :title="t('pages.overview.title')"
      :description="t('pages.overview.description')"
    >
      <template #actions>
        <VtButton variant="secondary" @click="emit('navigate', 'lab')"><VtIcon name="lab" :size="18" /> {{ t("overview.actions.openLab") }}</VtButton>
        <VtButton @click="emit('pair')"><VtIcon name="plus" :size="18" /> {{ t("shell.pair") }}</VtButton>
      </template>
    </VtPageHeader>

    <div class="overview-dashboard" data-page-section="summary">
      <VtOperationsHero
        :eyebrow="t('pages.overview.summaryEyebrow')"
        :title="t('pages.overview.summaryTitle')"
        :description="t('pages.overview.summaryDescription')"
        :value="onlineDevices"
        :value-label="t('overview.hero.onlineRobots')"
        :value-hint="t(ready ? 'overview.hero.apiReady' : 'overview.hero.apiAttention')"
        icon="telemetry"
      />
      <VtMetricStrip :items="overviewMetrics" />
      <div class="overview-quick-actions">
        <VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ ready ? t("shell.ready") : t("overview.hero.apiInterrupted") }}</VtBadge>
        <button class="text-link" type="button" @click="emit('navigate', 'devices')">{{ t("overview.actions.openDevices") }} <VtIcon name="arrow" :size="16" /></button>
      </div>
    </div>

    <div class="content-grid is-wide-left">
      <article class="vt-panel pipeline-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("overview.pipeline.eyebrow") }}</span><h2>{{ t("overview.pipeline.title") }}</h2></div><VtBadge tone="info">{{ t("overview.pipeline.lanFirst") }}</VtBadge></header>
        <div class="pipeline-list">
          <div v-for="(step, index) in pipeline" :key="step.kind" class="pipeline-step">
            <span>{{ String(index + 1).padStart(2, "0") }}</span><div><small>{{ step.kind }}</small><b>{{ step.title }}</b><p>{{ step.detail }}</p></div><i :class="{ active: ready && step.configured }"></i>
          </div>
        </div>
      </article>

      <article class="vt-panel agent-spotlight">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("overview.agent.eyebrow") }}</span><h2>{{ t("overview.agent.title") }}</h2></div></header>
        <template v-if="activeAgent">
          <div class="agent-monogram">{{ activeAgent.name.slice(0, 1).toUpperCase() }}</div>
          <h3>{{ activeAgent.name }}</h3><p>{{ activeAgent.persona }}</p>
          <div class="agent-meta"><span>{{ activeAgent.defaultLocale }}</span><span>{{ activeAgent.interactionMode }}</span><span>v{{ activeAgent.publishedVersion }}</span></div>
          <VtButton variant="secondary" @click="emit('navigate', 'agents')">{{ t("overview.agent.configure") }} <VtIcon name="arrow" :size="16" /></VtButton>
        </template>
        <VtEmptyState v-else icon="agent" :title="t('overview.agent.emptyTitle')" :text="t('overview.agent.emptyBody')" />
      </article>
    </div>

    <div class="content-grid">
      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("overview.fleet.eyebrow") }}</span><h2>{{ t("overview.fleet.title") }}</h2></div><button class="text-link" type="button" @click="emit('navigate', 'devices')">{{ t("overview.actions.viewAll") }} <VtIcon name="arrow" :size="15" /></button></header>
        <div v-if="devices.length" class="compact-list">
            <button v-for="device in devices.slice(0, 4)" :key="device.id" type="button" @click="emit('navigate', 'devices')">
            <span class="list-icon"><VtIcon name="device" :size="20" /></span><span><b>{{ device.name }}</b><small>{{ device.hardwareId }} · FW {{ device.firmwareVersion ?? "—" }}</small></span><VtBadge :tone="devicePresence(device).tone" dot>{{ devicePresence(device).label }}</VtBadge>
          </button>
        </div>
        <VtEmptyState v-else icon="device" :title="t('overview.fleet.emptyTitle')" :text="t('overview.fleet.emptyBody')"><VtButton size="sm" @click="emit('pair')">{{ t("overview.fleet.pairNow") }}</VtButton></VtEmptyState>
      </article>

      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("overview.events.eyebrow") }}</span><h2>{{ t("overview.events.title") }}</h2></div><button class="text-link" type="button" @click="emit('navigate', 'devices')">{{ t("overview.events.openDevice") }} <VtIcon name="arrow" :size="15" /></button></header>
        <div v-if="recentEvents.length" class="event-list compact">
          <div v-for="event in recentEvents" :key="event.id"><i></i><span><b>{{ event.eventType }}</b><small>{{ event.sessionId.slice(0, 12) }} · gen {{ event.generation }}</small></span><time>{{ formatDate(event.occurredAt) }}</time></div>
        </div>
        <VtEmptyState v-else icon="telemetry" :title="t('overview.events.emptyTitle')" :text="t('overview.events.emptyBody')" />
      </article>
    </div>
  </section>
</template>
