<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

import type { ConversationEvent, Device } from "../../api/schemas";
import { formatDate } from "../../utils/format";
import { VtBadge, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect } from "../ui";

const props = defineProps<{ devices: Device[]; events: ConversationEvent[]; selectedDeviceId: string; embedded?: boolean }>();
const emit = defineEmits<{ selectDevice: [id: string] }>();
const { t } = useI18n();

const search = ref("");
const selectedEventId = ref("");
const deviceEvents = computed(() => props.selectedDeviceId
  ? props.events.filter((event) => event.deviceId === props.selectedDeviceId)
  : props.events);
const filtered = computed(() => {
  const query = search.value.trim().toLowerCase();
  return [...deviceEvents.value].reverse().filter((event) => !query || `${event.eventType} ${event.sessionId} ${event.turnId ?? ""}`.toLowerCase().includes(query));
});
const selected = computed(() => filtered.value.find((event) => event.id === selectedEventId.value) ?? filtered.value[0]);
const sessions = computed(() => new Set(deviceEvents.value.map((event) => event.sessionId)).size);
const turns = computed(() => new Set(deviceEvents.value.flatMap((event) => event.turnId ? [event.turnId] : [])).size);
</script>

<template>
  <section class="vt-page" :class="{ 'is-embedded': embedded }" data-page="telemetry">
    <VtPageHeader v-if="!embedded" :eyebrow="t('telemetry.eyebrow')" :title="t('telemetry.title')" :description="t('telemetry.description')" />
    <header v-else class="device-subpage-header"><span class="vt-kicker">{{ t("telemetry.eyebrow") }}</span><h2>{{ t("telemetry.embeddedTitle", { device: devices.find((device) => device.id === selectedDeviceId)?.name }) }}</h2><p>{{ t("telemetry.embeddedDescription") }}</p></header>

    <div class="telemetry-summary"><article><span>{{ t("telemetry.summary.events") }}</span><b>{{ deviceEvents.length }}</b></article><article><span>{{ t("telemetry.summary.sessions") }}</span><b>{{ sessions }}</b></article><article><span>{{ t("telemetry.summary.turns") }}</span><b>{{ turns }}</b></article><article><span>{{ t("telemetry.summary.device") }}</span><b>{{ devices.find((item) => item.id === selectedDeviceId)?.name ?? "—" }}</b></article></div>

    <div class="telemetry-toolbar" :class="{ 'is-embedded': embedded }">
      <VtField v-if="!embedded" :label="t('telemetry.device')"><VtSelect :model-value="selectedDeviceId" @update:model-value="emit('selectDevice', String($event))"><option value="">{{ t("telemetry.noDevice") }}</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }}</option></VtSelect></VtField>
      <VtField :label="t('telemetry.filter')"><div class="input-with-icon"><VtIcon name="search" :size="17" /><VtInput v-model="search" :placeholder="t('telemetry.filterPlaceholder')" /></div></VtField>
      <VtBadge tone="info">{{ t("telemetry.refresh") }}</VtBadge>
    </div>

    <div v-if="filtered.length" class="telemetry-layout">
      <div class="trace-list">
        <button v-for="event in filtered" :key="event.id" type="button" :class="{ active: selected?.id === event.id }" @click="selectedEventId = event.id">
          <i></i><div><b>{{ event.eventType }}</b><small>{{ event.turnId ?? event.sessionId }}</small></div><span><em>gen {{ event.generation }}</em><time>{{ formatDate(event.occurredAt) }}</time></span>
        </button>
      </div>
      <article v-if="selected" class="vt-panel trace-detail">
        <header><div><span class="vt-kicker">{{ t("telemetry.detail.eyebrow") }}</span><h2>{{ selected.eventType }}</h2></div><VtBadge tone="info">generation {{ selected.generation }}</VtBadge></header>
        <dl><div><dt>{{ t("telemetry.detail.session") }}</dt><dd>{{ selected.sessionId }}</dd></div><div><dt>Turn</dt><dd>{{ selected.turnId ?? t("telemetry.detail.noTurn") }}</dd></div><div><dt>{{ t("telemetry.detail.occurredAt") }}</dt><dd>{{ formatDate(selected.occurredAt) }}</dd></div><div><dt>{{ t("telemetry.detail.device") }}</dt><dd>{{ selected.deviceId }}</dd></div></dl>
        <span class="vt-kicker">{{ t("telemetry.detail.payload") }}</span><pre>{{ JSON.stringify(selected.payload, null, 2) }}</pre>
      </article>
    </div>
    <VtEmptyState v-else icon="telemetry" :title="t('telemetry.emptyTitle')" :text="t('telemetry.emptyBody')" />
  </section>
</template>
