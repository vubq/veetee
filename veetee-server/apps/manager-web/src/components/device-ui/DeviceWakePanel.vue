<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

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

const { t } = useI18n();
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
  if (props.device.status === "offline") return t("wake.errors.offline");
  if (!wake.value) return t("wake.errors.noCapability");
  if (!selectedProfile.value) return t("wake.errors.noProfile");
  if (!artifact.value) return t("wake.errors.noArtifact");
  if (artifact.value.status !== "published") return t("wake.errors.artifactUnpublished");
  if (capabilities.value?.board !== artifact.value.board) return t("wake.errors.boardMismatch", { device: capabilities.value?.board, artifact: artifact.value.board });
  if (wake.value.runtime !== artifact.value.runtime || wake.value.runtimeAbi !== artifact.value.runtimeAbi) return t("wake.errors.runtimeMismatch", { runtime: `${wake.value.runtime}/${wake.value.runtimeAbi}` });
  if (wake.value.resourceAbi !== 1) return t("wake.errors.resourceAbiMismatch", { abi: wake.value.resourceAbi });
  if (artifact.value.sizeBytes > wake.value.slotBytes) return t("wake.errors.modelTooLarge");
  if (!wake.value.hotReload) return t("wake.errors.hotReload");
  return "";
});
const canApply = computed(() => !compatibilityIssue.value);
const deviceRollouts = computed(() => normalizeRollouts(
  props.rollouts.filter((rollout) => rollout.deviceId === props.device.id),
  [],
));

async function apply(): Promise<void> {
  if (!selectedProfile.value || !canApply.value) {
    error.value = compatibilityIssue.value || t("wake.errors.notReady");
    return;
  }
  busy.value = true;
  error.value = "";
  result.value = "";
  try {
    await props.rolloutWakeProfile(selectedProfile.value.id, [props.device.id]);
    result.value = t("wake.result.applied", { phrase: selectedProfile.value.activationPhrase, device: props.device.name });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("wake.errors.rolloutFailed");
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
        <header class="panel-header"><div><span class="vt-kicker">{{ t("wake.kicker") }}</span><h2>{{ t("wake.title", { device: device.name }) }}</h2><p>{{ t("wake.description") }}</p></div></header>
        <div v-if="publishedProfiles.length" class="wake-profile-picker">
          <button v-for="profile in publishedProfiles" :key="profile.id" type="button" :class="{ active: selectedProfile?.id === profile.id }" :aria-pressed="selectedProfile?.id === profile.id" @click="selectedProfileId = profile.id">
            <span><VtIcon name="mic" :size="18" /></span><div><b>“{{ profile.activationPhrase }}”</b><small>{{ profile.name }} · {{ profile.locale }} · v{{ profile.publishedVersion }}</small></div><VtBadge :tone="profile.productReady ? 'success' : 'warning'">{{ profile.productReady ? "ready" : "dev" }}</VtBadge>
          </button>
        </div>
        <VtEmptyState v-else icon="mic" :title="t('wake.emptyTitle')" :text="t('wake.emptyText')" />
        <div v-if="selectedProfile" class="wake-device-actions">
          <dl><div><dt>{{ t("wake.activation") }}</dt><dd>{{ selectedProfile.activation.detectorId }}</dd></div><div><dt>{{ t("wake.interrupt") }}</dt><dd>{{ selectedProfile.interrupt?.detectorId ?? t("common.notConfigured") }}</dd></div><div><dt>{{ t("wake.artifact") }}</dt><dd>{{ selectedProfile.artifactId }}</dd></div></dl>
          <VtButton :busy="busy" :disabled="!canApply" data-apply-wake-profile @click="apply"><VtIcon name="resource" :size="17" /> {{ t("wake.apply") }}</VtButton>
          <small v-if="result" class="desired-note" role="status">{{ result }}</small><small v-if="error" class="inline-error" role="alert">{{ error }}</small>
        </div>
      </article>

      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">{{ t("wake.historyKicker") }}</span><h2>{{ t("wake.historyTitle") }}</h2></div></header>
        <RolloutHistory :rollouts="deviceRollouts" compact  :empty-title="t('wake.emptyRolloutTitle')" :empty-text="t('wake.emptyRolloutText')" />
      </article>
    </div>
  </section>
</template>
