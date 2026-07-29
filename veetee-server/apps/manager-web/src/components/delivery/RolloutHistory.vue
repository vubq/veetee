<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";

import type { Device } from "../../api/schemas";
import type { DeliveryRollout } from "../../utils/rollouts";
import type { DeliveryRolloutKind, DeliveryRolloutStatus } from "../../utils/rollouts";
import { formatDate, statusTone } from "../../utils/format";
import { VtBadge, VtEmptyState, VtIcon } from "../ui";

const { t } = useI18n();
const props = withDefaults(defineProps<{
  rollouts: DeliveryRollout[];
  devices?: Device[];
  showDevice?: boolean;
  showKind?: boolean;
  compact?: boolean;
  emptyTitle?: string;
  emptyText?: string;
}>(), {
  devices: () => [],
  showDevice: false,
  showKind: true,
  compact: false,

});

const resolvedEmptyTitle = computed(() => props.emptyTitle ?? t("delivery.emptyTitle"));
const resolvedEmptyText = computed(() => props.emptyText ?? t("delivery.emptyText"));

function kindLabel(kind: DeliveryRolloutKind): string { return t(kind === "wake" ? "delivery.kind.wake" : "delivery.kind.ui"); }
function statusLabel(status: DeliveryRolloutStatus): string { return t(`delivery.status.${status}`); }

const sorted = computed(() => [...props.rollouts].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt)));

function deviceName(deviceId: string): string {
  return props.devices.find((device) => device.id === deviceId)?.name ?? deviceId;
}
</script>

<template>
  <div v-if="sorted.length" class="delivery-rollout-list" :class="{ 'is-compact': compact }" data-rollout-history>
    <article v-for="rollout in sorted" :key="`${rollout.kind}-${rollout.id}`" :data-rollout-kind="rollout.kind" :data-rollout-status="rollout.status">
      <span class="delivery-rollout-icon"><VtIcon :name="rollout.kind === 'ui' ? 'display' : 'mic'" :size="19" /></span>
      <div class="delivery-rollout-main">
        <div class="delivery-rollout-heading">
          <span v-if="showKind" class="delivery-rollout-kind">{{ kindLabel(rollout.kind) }}</span>
          <VtBadge :tone="statusTone(rollout.status)" dot>{{ statusLabel(rollout.status) }}</VtBadge>
        </div>
        <b>{{ rollout.artifactId }}</b>
      </div>
      <dl class="delivery-rollout-meta">
        <div v-if="showDevice"><dt>{{ t("delivery.device") }}</dt><dd>{{ deviceName(rollout.deviceId) }}</dd></div>
        <div><dt>{{ t("delivery.desiredRevision") }}</dt><dd>v{{ rollout.desiredStateVersion }}</dd></div>
        <div><dt>{{ t("delivery.createdAt") }}</dt><dd><time>{{ formatDate(rollout.createdAt) }}</time></dd></div>
      </dl>
    </article>
  </div>
  <VtEmptyState v-else class="delivery-rollout-empty" icon="telemetry" :title="resolvedEmptyTitle" :text="resolvedEmptyText" />
</template>
