<script setup lang="ts">
import { computed } from "vue";

import type { Device } from "../../api/schemas";
import type { DeliveryRollout } from "../../utils/rollouts";
import { rolloutKindLabel, rolloutStatusLabel } from "../../utils/rollouts";
import { formatDate, statusTone } from "../../utils/format";
import { VtBadge, VtEmptyState, VtIcon } from "../ui";

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
  emptyTitle: "Chưa có rollout",
  emptyText: "Publish artifact rồi chọn thiết bị đích để tạo desired state mới.",
});

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
          <span v-if="showKind" class="delivery-rollout-kind">{{ rolloutKindLabel(rollout.kind) }}</span>
          <VtBadge :tone="statusTone(rollout.status)" dot>{{ rolloutStatusLabel(rollout.status) }}</VtBadge>
        </div>
        <b>{{ rollout.artifactId }}</b>
      </div>
      <dl class="delivery-rollout-meta">
        <div v-if="showDevice"><dt>Thiết bị</dt><dd>{{ deviceName(rollout.deviceId) }}</dd></div>
        <div><dt>Desired revision</dt><dd>v{{ rollout.desiredStateVersion }}</dd></div>
        <div><dt>Thời điểm tạo</dt><dd><time>{{ formatDate(rollout.createdAt) }}</time></dd></div>
      </dl>
    </article>
  </div>
  <VtEmptyState v-else class="delivery-rollout-empty" icon="telemetry" :title="emptyTitle" :text="emptyText" />
</template>
