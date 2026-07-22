<script setup lang="ts">
import { computed, ref } from "vue";

import {
  deviceCapabilitiesSchema,
  type Artifact,
  type Device,
  type ResourceRollout,
  type WakeProfile,
} from "../../api/schemas";
import { formatBytes } from "../../utils/format";
import { normalizeRollouts } from "../../utils/rollouts";
import RolloutHistory from "../delivery/RolloutHistory.vue";
import { VtBadge, VtButton, VtEmptyState, VtIcon } from "../ui";

const props = defineProps<{
  device: Device;
  artifacts: Artifact[];
  profiles: WakeProfile[];
  rollouts: ResourceRollout[];
  rolloutWakeProfile: (id: string, deviceIds: string[]) => Promise<void>;
}>();

const selectedProfileId = ref("");
const busy = ref(false);
const result = ref("");
const error = ref("");
const publishedProfiles = computed(() => props.profiles.filter((profile) => profile.publishedVersion > 0));
const selectedProfile = computed(() => publishedProfiles.value.find((profile) => profile.id === selectedProfileId.value) ?? publishedProfiles.value[0]);
const artifact = computed(() => props.artifacts.find((item) => item.id === selectedProfile.value?.artifactId));
const capabilities = computed(() => {
  const parsed = deviceCapabilitiesSchema.safeParse(props.device.reportedState.state.capabilities);
  return parsed.success ? parsed.data : undefined;
});
const wake = computed(() => capabilities.value?.wake);
const compatibilityIssue = computed(() => {
  if (props.device.status === "offline") return "Thiết bị đang offline.";
  if (!wake.value) return "Thiết bị chưa report wake capability.";
  if (!selectedProfile.value) return "Chưa có wake profile đã publish.";
  if (!artifact.value) return "Model artifact của profile không tồn tại trong catalog.";
  if (artifact.value.status !== "published") return "Model artifact chưa được publish.";
  if (capabilities.value?.board !== artifact.value.board) return `Board ${capabilities.value?.board} không khớp artifact ${artifact.value.board}.`;
  if (wake.value.runtime !== artifact.value.runtime || wake.value.runtimeAbi !== artifact.value.runtimeAbi) return `Runtime ${wake.value.runtime}/${wake.value.runtimeAbi} không tương thích.`;
  if (wake.value.resourceAbi !== 1) return `Resource ABI ${wake.value.resourceAbi} không tương thích.`;
  if (artifact.value.sizeBytes > wake.value.slotBytes) return "Model lớn hơn resource slot của thiết bị.";
  if (!wake.value.hotReload) return "Firmware không hỗ trợ hot-reload wake model.";
  return "";
});
const canApply = computed(() => !compatibilityIssue.value);
const deviceRollouts = computed(() => normalizeRollouts(
  props.rollouts.filter((rollout) => rollout.deviceId === props.device.id),
  [],
));

async function apply(): Promise<void> {
  if (!selectedProfile.value || !canApply.value) {
    error.value = compatibilityIssue.value || "Wake profile chưa sẵn sàng.";
    return;
  }
  busy.value = true;
  error.value = "";
  result.value = "";
  try {
    await props.rolloutWakeProfile(selectedProfile.value.id, [props.device.id]);
    result.value = `Đã đặt “${selectedProfile.value.activationPhrase}” làm desired wake profile cho ${props.device.name}.`;
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "Không thể rollout wake profile.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="device-subpage wake-device-panel" data-device-panel="wake">
    <div class="capability-gate" :class="{ ready: canApply }">
      <span><VtIcon :name="canApply ? 'check' : 'warning'" :size="20" /></span>
      <div><b>{{ canApply ? `${device.name} sẵn sàng nhận wake profile` : "Chưa thể cập nhật wake word" }}</b><p>{{ canApply ? `${wake?.runtime} ABI ${wake?.runtimeAbi} · ${wake?.sampleRateHz} Hz mono · slot ${formatBytes(wake?.slotBytes ?? 0)}` : compatibilityIssue }}</p></div>
      <VtBadge :tone="canApply ? 'success' : 'warning'" dot>{{ device.status }}</VtBadge>
    </div>

    <div class="content-grid is-wide-left">
      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">DEVICE WAKE PROFILE</span><h2>Chọn profile cho {{ device.name }}</h2><p>Activation và interrupt là hai detector/policy riêng; phrase text không tự tạo model.</p></div></header>
        <div v-if="publishedProfiles.length" class="wake-profile-picker">
          <button v-for="profile in publishedProfiles" :key="profile.id" type="button" :class="{ active: selectedProfile?.id === profile.id }" @click="selectedProfileId = profile.id">
            <span><VtIcon name="mic" :size="18" /></span><div><b>“{{ profile.activationPhrase }}”</b><small>{{ profile.name }} · {{ profile.locale }} · v{{ profile.publishedVersion }}</small></div><VtBadge :tone="profile.productReady ? 'success' : 'warning'">{{ profile.productReady ? "ready" : "dev" }}</VtBadge>
          </button>
        </div>
        <VtEmptyState v-else icon="mic" title="Chưa có wake profile đã publish" text="Tạo và publish profile trong catalog Tài nguyên trước khi áp dụng cho thiết bị." />
        <div v-if="selectedProfile" class="wake-device-actions">
          <dl><div><dt>Activation</dt><dd>{{ selectedProfile.activation.detectorId }}</dd></div><div><dt>Interrupt</dt><dd>{{ selectedProfile.interrupt.detectorId }}</dd></div><div><dt>Artifact</dt><dd>{{ selectedProfile.artifactId }}</dd></div></dl>
          <VtButton :busy="busy" :disabled="!canApply" data-apply-wake-profile @click="apply"><VtIcon name="resource" :size="17" /> Áp dụng wake profile</VtButton>
          <small v-if="result" class="desired-note" role="status">{{ result }}</small><small v-if="error" class="inline-error" role="alert">{{ error }}</small>
        </div>
      </article>

      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">DEVICE ROLLOUT HISTORY</span><h2>Lịch sử wake</h2></div></header>
        <RolloutHistory :rollouts="deviceRollouts" compact empty-title="Chưa có wake rollout" empty-text="Rollout chỉ hoàn tất sau khi firmware verify, apply và report active đúng model version." />
      </article>
    </div>
  </section>
</template>
