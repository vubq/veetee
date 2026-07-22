<script setup lang="ts">
import { computed, ref } from "vue";

import type { AuditEvent, Device, OperationsProfile } from "../../api/schemas";
import { devicePresence } from "../../utils/device-presence";
import { formatDate } from "../../utils/format";
import { VtBadge, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader } from "../ui";

const props = defineProps<{
  devices: Device[];
  auditEvents: AuditEvent[];
  profile: OperationsProfile | undefined;
  ready: boolean;
}>();

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
    const version = device.firmwareVersion ?? "Chưa report";
    groups.set(version, [...(groups.get(version) ?? []), device]);
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right, undefined, { numeric: true }));
});

function details(event: AuditEvent): string {
  const keys = Object.keys(event.details);
  return keys.length ? keys.map((key) => `${key}: ${String(event.details[key])}`).join(" · ") : "Metadata đã redact";
}
</script>

<template>
  <section class="vt-page operations-page" data-page="operations">
    <VtPageHeader eyebrow="OPERATIONS / AUDIT & PRIVACY" title="Vận hành minh bạch, không cần domain" description="Một màn hình read-only để kiểm tra đường LAN/Tailscale, privacy policy, firmware inventory và audit mutation của workspace.">
      <template #actions><VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ ready ? "Manager API sẵn sàng" : "Manager API gián đoạn" }}</VtBadge></template>
    </VtPageHeader>

    <div class="operations-hero">
      <div><span class="vt-kicker">LAN-FIRST / TAILSCALE PRIVATE</span><h2>Không cần mua domain để vận hành Veetee.</h2><p>Web, Manager API và voice WebSocket lấy endpoint từ environment/bootstrap. Đổi IP LAN hoặc Tailscale không cần build lại firmware.</p></div>
      <div class="operations-hero-mark"><VtIcon name="telemetry" :size="32" /><b>0</b><small>PUBLIC DOMAINS REQUIRED</small></div>
    </div>

    <div class="operations-grid">
      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">RUNTIME TOPOLOGY</span><h2>Đường kết nối hiện tại</h2></div><VtBadge tone="info">single-node</VtBadge></header>
        <dl v-if="profile" class="operations-facts">
          <div><dt>Manager API</dt><dd>{{ profile.deployment.managerApiUrl }}</dd></div>
          <div><dt>Voice WebSocket</dt><dd>{{ profile.deployment.voiceWebsocketUrl }}</dd></div>
          <div><dt>Firmware OTA</dt><dd>{{ profile.firmware.otaRoute }} · {{ profile.firmware.configuredVersion }}</dd></div>
        </dl>
        <VtEmptyState v-else icon="telemetry" title="Chưa đọc được runtime profile" text="Thử lại khi Manager API sẵn sàng." />
      </article>

      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">PRIVACY & SECURITY</span><h2>Chính sách an toàn</h2></div><VtBadge tone="success" dot>redacted by default</VtBadge></header>
        <div v-if="profile" class="policy-list">
          <div><VtIcon name="check" :size="18" /><span><b>Raw audio</b><small>{{ profile.privacy.rawAudioStored ? "Đang lưu" : "Không lưu" }}</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>Transcript</b><small>{{ profile.privacy.transcriptStored ? "Đang lưu" : "Không lưu" }}</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>Conversation metadata</b><small>Retention {{ profile.privacy.conversationEventRetentionDays }} ngày</small></span></div>
          <div><VtIcon name="check" :size="18" /><span><b>Artifacts</b><small>{{ profile.security.signedArtifacts ? "Ed25519 + SHA-256" : "Chưa bật signing" }}</small></span></div>
        </div>
      </article>
    </div>

    <div class="operations-grid">
      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">FIRMWARE INVENTORY</span><h2>Phiên bản đang có trên fleet</h2><p>Read-only inventory; firmware rollout admin chỉ bật khi release artifact đã ký.</p></div><VtBadge :tone="profile?.firmware.releaseConfigured ? 'success' : 'warning'">{{ profile?.firmware.releaseConfigured ? "Release đã cấu hình" : "Chưa có release OTA" }}</VtBadge></header>
        <div v-if="firmwareGroups.length" class="firmware-groups"><div v-for="([version, group]) in firmwareGroups" :key="version"><span class="release-token">{{ version }}</span><span>{{ group.length }} thiết bị</span><span>{{ group.filter((device) => devicePresence(device).state === 'online').length }} online</span></div></div>
        <VtEmptyState v-else icon="device" title="Chưa có firmware inventory" text="Thiết bị sẽ xuất hiện sau khi pairing và bootstrap report." />
      </article>

      <article class="vt-panel operations-panel">
        <header class="panel-header"><div><span class="vt-kicker">DEVICE HEALTH</span><h2>Fleet signal</h2></div></header>
        <div class="operations-metric-grid"><div><span>Fresh online</span><b>{{ devices.filter((device) => devicePresence(device).state === 'online').length }}</b></div><div><span>Stale report</span><b>{{ devices.filter((device) => devicePresence(device).state === 'stale').length }}</b></div><div><span>Offline</span><b>{{ devices.filter((device) => devicePresence(device).state === 'offline').length }}</b></div></div>
      </article>
    </div>

    <article class="vt-panel audit-panel">
      <header class="panel-header"><div><span class="vt-kicker">AUDIT TRAIL</span><h2>Mutation đã được ghi nhận</h2><p>Chỉ hiển thị action, target, request ID và metadata đã lọc; secret, token, audio, transcript và arguments không xuất hiện.</p></div><VtBadge tone="info">{{ filteredAudit.length }} bản ghi</VtBadge></header>
      <div class="audit-toolbar"><VtField label="Lọc action"><VtInput v-model="actionFilter" placeholder="artifact.publish, device.pair…" /></VtField><VtField label="Target type"><VtInput v-model="targetFilter" placeholder="device, agent, provider…" /></VtField></div>
      <div v-if="filteredAudit.length" class="audit-list"><div v-for="event in filteredAudit" :key="event.id" class="audit-row"><span class="audit-dot"></span><div><b>{{ event.action }}</b><small>{{ event.targetType }} · {{ event.targetId }} · req {{ event.requestId }} · {{ event.actorName ?? "system" }}</small><p>{{ details(event) }}</p></div><time>{{ formatDate(event.createdAt) }}</time></div></div>
      <VtEmptyState v-else icon="telemetry" title="Chưa có audit phù hợp" text="Các mutation từ pairing, provider, agent, MCP và resource rollout sẽ xuất hiện tại đây." />
    </article>
  </section>
</template>
