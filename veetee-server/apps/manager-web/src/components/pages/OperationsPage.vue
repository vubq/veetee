<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

import type { AuditEvent, Device, OperationsProfile } from "../../api/schemas";
import { devicePresence } from "../../utils/device-presence";
import { formatDate } from "../../utils/format";
import { VtBadge, VtEmptyState, VtField, VtInput, VtMetricStrip, VtOperationsHero, VtPageHeader } from "../ui";

const props = defineProps<{
  devices: Device[];
  auditEvents: AuditEvent[];
  profile: OperationsProfile | undefined;
  ready: boolean;
}>();
const { t } = useI18n();

const actionFilter = ref("");
const targetFilter = ref("");

const filteredAudit = computed(() => {
  const action = actionFilter.value.trim().toLowerCase();
  const target = targetFilter.value.trim().toLowerCase();
  return props.auditEvents.filter((event) =>
    (!action || event.action.toLowerCase().includes(action)) &&
    (!target || event.targetType.toLowerCase() === target),
  );
});

const firmwareGroups = computed(() => {
  const groups = new Map<string, Device[]>();
  for (const device of props.devices) {
    const version = device.firmwareVersion ?? t("operations.firmware.notReported");
    groups.set(version, [...(groups.get(version) ?? []), device]);
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }));
});
const fleetMetrics = computed(() => [
  {
    label: t("operations.metrics.online"),
    value: props.devices.filter((device) => devicePresence(device).state === "online").length,
    detail: t("operations.metrics.onlineDetail"),
    tone: "success" as const,
  },
  {
    label: t("operations.metrics.stale"),
    value: props.devices.filter((device) => devicePresence(device).state === "stale").length,
    detail: t("operations.metrics.staleDetail"),
    tone: "warning" as const,
  },
  {
    label: t("operations.metrics.offline"),
    value: props.devices.filter((device) => devicePresence(device).state === "offline").length,
    detail: t("operations.metrics.offlineDetail"),
    tone: "neutral" as const,
  },
]);

function details(event: AuditEvent): string {
  const keys = Object.keys(event.details);
  return keys.length ? keys.map((key) => `${key}: ${String(event.details[key])}`).join(" · ") : t("operations.audit.redactedMetadata");
}
</script>

<template>
  <section class="vt-page operations-page" data-page="operations">
    <VtPageHeader :eyebrow="t('pages.operations.eyebrow')" :title="t('pages.operations.title')" :description="t('pages.operations.description')">
      <template #actions><VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ t(ready ? "operations.api.ready" : "operations.api.interrupted") }}</VtBadge></template>
    </VtPageHeader>

    <div class="operations-dashboard" data-page-section="summary">
      <VtOperationsHero
        :eyebrow="t('operations.hero.eyebrow')"
        :title="t('operations.hero.title')"
        :description="t('operations.hero.description')"
        value="0"
        :value-label="t('operations.hero.publicDomains')"
        :value-hint="t(ready ? 'operations.api.ready' : 'operations.api.attention')"
        icon="telemetry"
      />
      <VtMetricStrip :items="fleetMetrics" />
    </div>

    <div class="operations-grid">
      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("operations.runtime.eyebrow") }}</span><h2>{{ t("operations.runtime.title") }}</h2></div><VtBadge tone="info">single-node</VtBadge></header>
        <dl v-if="profile" class="operations-facts">
          <div><dt>Manager API</dt><dd>{{ profile.deployment.managerApiUrl }}</dd></div>
          <div><dt>Voice WebSocket</dt><dd>{{ profile.deployment.voiceWebsocketUrl }}</dd></div>
          <div><dt>Firmware OTA</dt><dd>{{ profile.firmware.otaRoute }} · {{ profile.firmware.configuredVersion }}</dd></div>
        </dl>
        <VtEmptyState v-else icon="telemetry" :title="t('operations.runtime.emptyTitle')" :text="t('operations.runtime.emptyBody')" />
      </article>

      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("operations.privacy.eyebrow") }}</span><h2>{{ t("operations.privacy.title") }}</h2></div><VtBadge tone="success" dot>{{ t("operations.privacy.redacted") }}</VtBadge></header>
        <div v-if="profile" class="policy-list">
          <div><VtIcon name="check" :size="18" /><span><b>{{ t("operations.privacy.rawAudio") }}</b><small>{{ t(profile.privacy.rawAudioStored ? "operations.privacy.stored" : "operations.privacy.notStored") }}</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>{{ t("operations.privacy.transcript") }}</b><small>{{ t(profile.privacy.transcriptStored ? "operations.privacy.stored" : "operations.privacy.notStored") }}</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>{{ t("operations.privacy.metadata") }}</b><small>{{ t("operations.privacy.retention", { days: profile.privacy.conversationEventRetentionDays }) }}</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>{{ t("operations.privacy.artifacts") }}</b><small>{{ profile.security.signedArtifacts ? "Ed25519 + SHA-256" : t("operations.privacy.signingDisabled") }}</small></span></div>
        </div>
      </article>
    </div>

    <article class="vt-panel operations-panel" data-page-section="firmware">
      <header class="panel-header"><div><span class="vt-kicker">{{ t("operations.firmware.eyebrow") }}</span><h2>{{ t("operations.firmware.title") }}</h2><p>{{ t("operations.firmware.description") }}</p></div><VtBadge :tone="profile?.firmware.releaseConfigured ? 'success' : 'warning'">{{ t(profile?.firmware.releaseConfigured ? "operations.firmware.configured" : "operations.firmware.notConfigured") }}</VtBadge></header>
      <div v-if="firmwareGroups.length" class="firmware-groups"><div v-for="([version, group]) in firmwareGroups" :key="version"><span class="release-token">{{ version }}</span><span>{{ t("operations.firmware.deviceCount", { count: group.length }) }}</span><span>{{ t("operations.firmware.onlineCount", { count: group.filter((device) => devicePresence(device).state === 'online').length }) }}</span></div></div>
      <VtEmptyState v-else icon="device" :title="t('operations.firmware.emptyTitle')" :text="t('operations.firmware.emptyBody')" />
    </article>

    <article class="vt-panel audit-panel" data-page-section="audit">
      <header class="panel-header"><div><span class="vt-kicker">{{ t("operations.audit.eyebrow") }}</span><h2>{{ t("operations.audit.title") }}</h2><p>{{ t("operations.audit.description") }}</p></div><VtBadge tone="info">{{ t("operations.audit.recordCount", { count: filteredAudit.length }) }}</VtBadge></header>
      <div class="audit-toolbar"><VtField :label="t('operations.audit.actionFilter')"><VtInput v-model="actionFilter"  :placeholder="t('operations.audit.actionPlaceholder')" /></VtField><VtField :label="t('operations.audit.targetFilter')"><VtInput v-model="targetFilter"  :placeholder="t('operations.audit.targetPlaceholder')" /></VtField></div>
      <div v-if="filteredAudit.length" class="audit-list"><div v-for="event in filteredAudit" :key="event.id" class="audit-row"><span class="audit-dot"></span><div><b>{{ event.action }}</b><small>{{ event.targetType }} · {{ event.targetId }} · req {{ event.requestId }} · {{ event.actorName ?? "system" }}</small><p>{{ details(event) }}</p></div><time>{{ formatDate(event.createdAt) }}</time></div></div>
      <VtEmptyState v-else icon="telemetry" :title="t('operations.audit.emptyTitle')" :text="t('operations.audit.emptyBody')" />
    </article>
  </section>
</template>
