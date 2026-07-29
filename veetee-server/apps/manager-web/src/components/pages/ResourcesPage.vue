<script setup lang="ts">
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/vue";
import { computed, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";

import type { Artifact, Device, FirmwareRelease, FirmwareRollout, ResourceRollout, UiPackRollout, WakeProfile } from "../../api/schemas";
import { formatBytes, statusTone } from "../../utils/format";
import { normalizeRollouts } from "../../utils/rollouts";
import RolloutHistory from "../delivery/RolloutHistory.vue";
import { VtBadge, VtButton, VtEmptyState, VtField, VtIcon, VtInput, VtMetricStrip, VtOperationsHero, VtPageHeader, VtSelect, VtSwitch } from "../ui";

const props = defineProps<{
  artifacts: Artifact[];
  wakeProfiles: WakeProfile[];
  rollouts: ResourceRollout[];
  uiPackRollouts: UiPackRollout[];
  firmwareReleases: FirmwareRelease[];
  firmwareRollouts: FirmwareRollout[];
  devices: Device[];
  registerArtifact: (artifactId: string, license: string) => Promise<void>;
  publishArtifact: (id: string) => Promise<void>;
  createWakeProfile: (input: {
    artifactId: string; name: string; locale: string; channel: string; activationPhrase: string;
    sendWakeAudio: boolean;
    activation: { detectorId: string; sensitivity: number; cooldownMs: number; allowedStates: string[] };
    interrupt: { detectorId: string; sensitivity: number; cooldownMs: number; allowedStates: string[] } | null;
  }) => Promise<void>;
  publishWakeProfile: (id: string) => Promise<void>;
  publishFirmwareRelease: (id: string) => Promise<void>;
  createFirmwareRollout: (artifactId: string, percentage: number, canaryDeviceIds: string[]) => Promise<void>;
  pauseFirmwareRollout: (id: string) => Promise<void>;
  resumeFirmwareRollout: (id: string, percentage?: number) => Promise<void>;
  rollbackFirmwareRollout: (id: string) => Promise<void>;
}>();
const { t } = useI18n();

const artifactForm = reactive({ id: "", license: "" });
const wakeForm = reactive({ name: "Hey VeeTee", artifactId: "", phrase: "Hey VeeTee", channel: "development", locale: "vi-VN", activationDetector: "", interruptDetector: "", sendWakeAudio: false });
const busyKey = ref("");
const error = ref("");
const firmwareForm = reactive({ artifactId: "", canaryDeviceId: "", percentage: 10 });

const wakeArtifacts = computed(() => props.artifacts.filter((artifact) => ["resource_bundle", "model_pack"].includes(artifact.kind)));
const deliveryRollouts = computed(() => normalizeRollouts(props.rollouts, props.uiPackRollouts));
function canaryReady(rollout: FirmwareRollout): boolean {
  return rollout.canaryDeviceIds.every((deviceId) => rollout.activeDeviceIds.includes(deviceId));
}
function percentagePending(rollout: FirmwareRollout): boolean {
  if (rollout.status !== "running" || rollout.percentage <= 0 || !canaryReady(rollout)) return false;
  const canaries = new Set(rollout.canaryDeviceIds);
  return rollout.selectedDeviceIds.every((deviceId) => canaries.has(deviceId));
}
const activeRollouts = computed(() => deliveryRollouts.value.filter((rollout) => rollout.status === "active").length);
const completeRollouts = computed(() => deliveryRollouts.value.filter((rollout) => rollout.status === "complete").length);
const problemRollouts = computed(() => deliveryRollouts.value.filter((rollout) => ["failed", "rolled_back"].includes(rollout.status)).length);
const problemFirmwareRollouts = computed(() => props.firmwareRollouts.filter((rollout) => ["failed", "rolled_back"].includes(rollout.status)).length);
const allProblemRollouts = computed(() => problemRollouts.value + problemFirmwareRollouts.value);
const rolloutMetrics = computed(() => [
  { label: t("resources.metrics.pending"), value: activeRollouts.value, detail: t("resources.metrics.pendingDetail"), tone: activeRollouts.value ? "warning" as const : "neutral" as const },
  { label: t("resources.metrics.applied"), value: completeRollouts.value, detail: t("resources.metrics.appliedDetail"), tone: "success" as const },
  { label: t("resources.metrics.problem"), value: problemRollouts.value, detail: t("resources.metrics.problemDetail"), tone: problemRollouts.value ? "danger" as const : "neutral" as const },
]);
const resourceMetrics = computed(() => [
  {
    label: t("resources.metrics.artifacts"),
    value: props.artifacts.length,
    detail: t("resources.metrics.publishedCount", { count: props.artifacts.filter((artifact) => artifact.status === "published").length }),
    tone: "info" as const,
  },
  {
    label: t("resources.metrics.wakeProfiles"),
    value: props.wakeProfiles.length,
    detail: t("resources.metrics.productReadyCount", { count: props.wakeProfiles.filter((profile) => profile.productReady).length }),
    tone: props.wakeProfiles.some((profile) => !profile.productReady) ? "warning" as const : "success" as const,
  },
  {
    label: t("resources.metrics.delivery"),
    value: deliveryRollouts.value.length + props.firmwareRollouts.length,
    detail: t("resources.metrics.deliveryDetail"),
    tone: allProblemRollouts.value ? "danger" as const : "neutral" as const,
  },
]);

async function perform(key: string, action: () => Promise<void>): Promise<void> {
  busyKey.value = key;
  error.value = "";
  try { await action(); }
  catch (exception) { error.value = exception instanceof Error ? exception.message : t("resources.errors.operationFailed"); }
  finally { busyKey.value = ""; }
}

async function register(): Promise<void> {
  await perform("register", async () => {
    await props.registerArtifact(artifactForm.id.trim(), artifactForm.license.trim());
    artifactForm.id = ""; artifactForm.license = "";
  });
}

async function createWake(): Promise<void> {
  await perform("create-wake", async () => {
    await props.createWakeProfile({
      artifactId: wakeForm.artifactId,
      name: wakeForm.name.trim(), locale: wakeForm.locale, channel: wakeForm.channel,
      activationPhrase: wakeForm.phrase.trim(),
      sendWakeAudio: wakeForm.sendWakeAudio,
      activation: { detectorId: wakeForm.activationDetector.trim(), sensitivity: 0.55, cooldownMs: 1500, allowedStates: ["standby"] },
      interrupt: wakeForm.interruptDetector.trim()
        ? { detectorId: wakeForm.interruptDetector.trim(), sensitivity: 0.62, cooldownMs: 800, allowedStates: ["thinking", "speaking"] }
        : null,
    });
  });
}

async function createFirmware(): Promise<void> {
  await perform("firmware-create", async () => {
    const release = props.firmwareReleases.find((item) => item.id === firmwareForm.artifactId);
    const canaries = firmwareForm.canaryDeviceId ? [firmwareForm.canaryDeviceId] : [];
    if (release?.channel === "stable" && canaries.length === 0) {
      throw new Error(t("resources.errors.canaryRequired"));
    }
    await props.createFirmwareRollout(
      firmwareForm.artifactId,
      Number(firmwareForm.percentage),
      canaries,
    );
  });
}

</script>

<template>
  <section class="vt-page" data-page="resources">
    <VtPageHeader :eyebrow="t('pages.resources.eyebrow')" :title="t('pages.resources.title')" :description="t('pages.resources.description')" />
    <div class="resource-dashboard" data-page-section="summary">
      <VtOperationsHero
        :eyebrow="t('resources.hero.eyebrow')"
        :title="t('resources.hero.title')"
        :description="t('resources.hero.description')"
        :value="artifacts.length"
        :value-label="t('resources.hero.artifacts')"
        :value-hint="t('resources.hero.deliveryCount', { count: deliveryRollouts.length + firmwareRollouts.length })"
        icon="resource"
      />
      <VtMetricStrip :items="resourceMetrics" />
    </div>
    <p v-if="error" class="inline-error page-error" role="alert">{{ error }}</p>

    <TabGroup as="div" class="resource-tabs" data-page-section="primary-workspace">
      <TabList class="vt-tabs">
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="resource" :size="17" /> {{ t("resources.tabs.artifacts") }} <span>{{ artifacts.length }}</span></button></Tab>
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="mic" :size="17" /> {{ t("resources.tabs.wakeProfiles") }} <span>{{ wakeProfiles.length }}</span></button></Tab>
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="telemetry" :size="17" /> {{ t("resources.tabs.rollouts") }} <span>{{ deliveryRollouts.length }}</span></button></Tab>
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="upload" :size="17" /> {{ t("resources.tabs.firmware") }} <span>{{ firmwareRollouts.length }}</span></button></Tab>
      </TabList>

      <TabPanels>
        <TabPanel class="tab-panel">
          <div class="content-grid is-wide-left">
            <article class="vt-panel">
              <header class="panel-header"><div><span class="vt-kicker">{{ t("resources.artifacts.eyebrow") }}</span><h2>{{ t("resources.artifacts.title") }}</h2></div></header>
              <div v-if="artifacts.length" class="artifact-list">
                <div v-for="artifact in artifacts" :key="artifact.id">
                  <span class="artifact-icon"><VtIcon :name="artifact.kind === 'display_assets' ? 'display' : 'resource'" :size="20" /></span>
                  <div><b>{{ artifact.id }}</b><small>{{ artifact.kind }} · {{ artifact.version }} · {{ formatBytes(artifact.sizeBytes) }}</small><code>{{ artifact.sha256.slice(0, 18) }}…</code></div>
                  <span><VtBadge :tone="statusTone(artifact.status)">{{ artifact.status }}</VtBadge><small>{{ artifact.channel }}</small></span>
                  <VtButton v-if="artifact.status === 'validated'" size="sm" variant="secondary" :busy="busyKey === `publish-${artifact.id}`" @click="perform(`publish-${artifact.id}`, () => publishArtifact(artifact.id))">{{ t("common.publish") }}</VtButton>
                </div>
              </div>
              <VtEmptyState v-else icon="resource" :title="t('resources.artifacts.emptyTitle')" :text="t('resources.artifacts.emptyBody')" />
            </article>
            <article class="vt-panel form-section">
              <header class="panel-header"><div><span class="vt-kicker">{{ t("resources.register.eyebrow") }}</span><h2>{{ t("resources.register.title") }}</h2><p>{{ t("resources.register.description") }}</p></div></header>
              <form class="form-stack" @submit.prevent="register">
                <VtField :label="t('resources.register.artifactId')" required><VtInput v-model="artifactForm.id" placeholder="wake-vi-1.0.0" required /></VtField>
                <VtField :label="t('resources.register.license')" required><VtInput v-model="artifactForm.license" placeholder="Internal model pack · corpus approved" required /></VtField>
                <VtButton type="submit" :busy="busyKey === 'register'"><VtIcon name="plus" :size="16" /> {{ t("resources.register.action") }}</VtButton>
              </form>
            </article>
          </div>
        </TabPanel>

        <TabPanel class="tab-panel">
          <div class="content-grid is-wide-left">
            <article class="vt-panel">
              <header class="panel-header"><div><span class="vt-kicker">{{ t("resources.wake.eyebrow") }}</span><h2>{{ t("resources.wake.title") }}</h2></div></header>
              <div v-if="wakeProfiles.length" class="wake-list">
                <article v-for="profile in wakeProfiles" :key="profile.id">
                  <div class="wake-phrase"><span>“</span><b>{{ profile.activationPhrase }}</b><span>”</span></div>
                  <div class="wake-info"><h3>{{ profile.name }}</h3><p>{{ profile.locale }} · {{ profile.channel }} · artifact {{ profile.artifactId }}</p><div><VtBadge :tone="profile.productReady ? 'success' : 'warning'">{{ t(profile.productReady ? "resources.wake.productReady" : "resources.wake.notBenchmarked") }}</VtBadge><VtBadge tone="info">Activation ≠ Interrupt</VtBadge><VtBadge :tone="profile.sendWakeAudio ? 'warning' : 'neutral'">{{ t(profile.sendWakeAudio ? "resources.wake.audioEnabled" : "resources.wake.audioDisabled") }}</VtBadge></div></div>
                  <dl><div><dt>Activation</dt><dd>{{ profile.activation.detectorId }}</dd></div><div><dt>Interrupt</dt><dd>{{ profile.interrupt?.detectorId ?? t("common.notConfigured") }}</dd></div></dl>
                  <VtButton v-if="profile.publishedVersion === 0" size="sm" :busy="busyKey === `wake-publish-${profile.id}`" @click="perform(`wake-publish-${profile.id}`, () => publishWakeProfile(profile.id))">{{ t("common.publish") }} v{{ profile.version }}</VtButton>
                  <VtBadge v-else tone="info">Áp dụng trong Thiết bị</VtBadge>
                </article>
              </div>
              <VtEmptyState v-else icon="mic" :title="t('resources.wake.emptyTitle')" :text="t('resources.wake.emptyBody')" />
            </article>

            <article class="vt-panel form-section">
              <header class="panel-header"><div><span class="vt-kicker">NEW WAKE PROFILE</span><h2>Draft “Hey VeeTee”</h2><p>Hey VeeTee đọc là “hây vi ti”. Activation và interrupt dùng detector/policy riêng.</p></div></header>
              <form class="form-stack" @submit.prevent="createWake">
                <VtField label="Tên profile" required><VtInput v-model="wakeForm.name" required /></VtField>
                <VtField label="Model artifact" required><VtSelect v-model="wakeForm.artifactId" required><option value="">Chọn artifact</option><option v-for="artifact in wakeArtifacts" :key="artifact.id" :value="artifact.id">{{ artifact.id }} · {{ artifact.status }}</option></VtSelect></VtField>
                <div class="form-grid two"><VtField label="Activation phrase"><VtInput v-model="wakeForm.phrase" /></VtField><VtField label="Locale"><VtSelect v-model="wakeForm.locale"><option value="vi-VN">vi-VN</option><option value="en-US">en-US</option></VtSelect></VtField></div>
                <VtField label="Activation detector ID" required><VtInput v-model="wakeForm.activationDetector" placeholder="wn9s_hiesp" required /></VtField>
                <VtField label="Interrupt detector ID"><VtInput v-model="wakeForm.interruptDetector" :placeholder="t('common.notConfigured')" /></VtField>
                <VtSwitch v-model="wakeForm.sendWakeAudio" :label="t('resources.wake.audioOptIn')" :description="t('resources.wake.audioOptInDescription')" />
                <VtField label="Channel"><VtSelect v-model="wakeForm.channel"><option value="development">development</option><option value="canary">canary</option><option value="stable">stable · yêu cầu benchmark pass</option></VtSelect></VtField>
                <VtButton type="submit" :busy="busyKey === 'create-wake'" :disabled="!wakeForm.artifactId">Tạo wake profile draft</VtButton>
              </form>
            </article>
          </div>
        </TabPanel>

        <TabPanel class="tab-panel">
          <div class="rollout-dashboard">
            <VtOperationsHero
              eyebrow="DELIVERY CONTROL"
              title="Phân phối có xác nhận"
              description="Mọi thay đổi đi qua desired state, kiểm tra capability và chỉ hoàn tất khi firmware báo active đúng phiên bản."
              :value="deliveryRollouts.length"
              value-label="Tổng lượt phân phối"
              value-hint="Wake / model + UI Pack"
              icon="telemetry"
            />
            <VtMetricStrip :items="rolloutMetrics" />

            <article class="vt-panel rollout-history-panel">
              <header class="panel-header rollout-panel-header"><div><span class="vt-kicker">DESIRED DELIVERY</span><h2>Lịch sử rollout</h2><p>Timeline hợp nhất theo thiết bị. Mỗi bản ghi giữ nguyên ranh giới giữa yêu cầu desired và trạng thái firmware đã xác nhận.</p></div><VtBadge tone="info">Mới nhất trước</VtBadge></header>
              <RolloutHistory v-if="deliveryRollouts.length" :rollouts="deliveryRollouts" :devices="devices" show-device />
              <div v-else class="rollout-empty-guide">
                <div class="rollout-empty-copy"><span><VtIcon name="telemetry" :size="23" /></span><div><b>Chưa có lượt phân phối nào</b><p>Catalog đã sẵn sàng. Rollout chỉ xuất hiện sau khi bạn chọn một artifact đã publish và một thiết bị đích đủ capability.</p></div></div>
                <div class="rollout-empty-steps">
                  <span><b>01</b><strong>Phát hành tài nguyên</strong><small>Wake profile hoặc UI Pack đã ký.</small></span>
                  <i><VtIcon name="arrow" :size="17" /></i>
                  <span><b>02</b><strong>Chọn thiết bị</strong><small>Thực hiện trong tab Wake word hoặc Display / UI.</small></span>
                  <i><VtIcon name="arrow" :size="17" /></i>
                  <span><b>03</b><strong>Firmware xác nhận</strong><small>Hoàn tất khi report active đúng phiên bản.</small></span>
                </div>
              </div>
            </article>
          </div>
        </TabPanel>

        <TabPanel class="tab-panel">
          <div class="content-grid is-wide-left firmware-rollout-grid">
            <article class="vt-panel">
              <header class="panel-header"><div><span class="vt-kicker">SIGNED EXECUTABLE RELEASES</span><h2>Firmware A/B</h2><p>Chỉ image ESP32-S3 có manifest Ed25519 hợp lệ mới được publish. Desired và reported luôn tách biệt.</p></div><VtBadge tone="info">Không xoá NVS / Wi-Fi</VtBadge></header>
              <div v-if="firmwareReleases.length" class="artifact-list">
                <div v-for="release in firmwareReleases" :key="release.id">
                  <span class="artifact-icon"><VtIcon name="upload" :size="20" /></span>
                  <div><b>{{ release.version }}</b><small>{{ release.id }} · {{ release.channel }} · {{ formatBytes(release.sizeBytes) }}</small><code>{{ release.sha256.slice(0, 18) }}… · epoch {{ release.securityEpoch }}</code></div>
                  <VtBadge :tone="statusTone(release.status)">{{ release.status }}</VtBadge>
                  <VtButton v-if="release.status === 'validated'" size="sm" variant="secondary" :busy="busyKey === `fw-publish-${release.id}`" @click="perform(`fw-publish-${release.id}`, () => publishFirmwareRelease(release.id))">{{ t("common.publish") }}</VtButton>
                </div>
              </div>
              <VtEmptyState v-else icon="resource" title="Chưa có firmware release" text="Chạy firmware:release, đăng ký artifact, rồi publish tại đây." />

              <div v-if="firmwareRollouts.length" class="firmware-campaigns">
                <article v-for="rollout in firmwareRollouts" :key="rollout.id">
                  <header><div><b>{{ rollout.artifactId }}</b><small>{{ rollout.channel }} · {{ rollout.percentage }}% · {{ rollout.selectedDeviceIds.length }} thiết bị</small></div><VtBadge :tone="statusTone(rollout.status)">{{ rollout.status }}</VtBadge></header>
                  <div class="firmware-progress"><span :style="{ width: `${rollout.percentage}%` }"></span></div>
                  <p>Canary {{ rollout.canaryDeviceIds.length }} · Active {{ rollout.activeDeviceIds.length }} · Lỗi {{ rollout.failedDeviceIds.length }}</p>
                  <footer>
                    <VtButton v-if="rollout.status === 'running'" size="sm" variant="secondary" :busy="busyKey === `fw-pause-${rollout.id}`" @click="perform(`fw-pause-${rollout.id}`, () => pauseFirmwareRollout(rollout.id))">Pause</VtButton>
                    <VtButton v-if="rollout.status === 'paused'" size="sm" :busy="busyKey === `fw-resume-${rollout.id}`" @click="perform(`fw-resume-${rollout.id}`, () => resumeFirmwareRollout(rollout.id))">Resume</VtButton>
                    <VtButton v-if="percentagePending(rollout)" size="sm" :busy="busyKey === `fw-apply-${rollout.id}`" @click="perform(`fw-apply-${rollout.id}`, () => resumeFirmwareRollout(rollout.id, rollout.percentage))">Phân phối {{ rollout.percentage }}%</VtButton>
                    <VtButton v-if="rollout.status === 'running' && rollout.percentage < 100 && canaryReady(rollout)" size="sm" :busy="busyKey === `fw-expand-${rollout.id}`" @click="perform(`fw-expand-${rollout.id}`, () => resumeFirmwareRollout(rollout.id, Math.min(100, rollout.percentage + 10)))">Mở rộng +10%</VtButton>
                    <VtButton v-if="rollout.previousArtifactId && !['rolled_back', 'completed'].includes(rollout.status)" size="sm" variant="danger" :busy="busyKey === `fw-rollback-${rollout.id}`" @click="perform(`fw-rollback-${rollout.id}`, () => rollbackFirmwareRollout(rollout.id))">Rollback</VtButton>
                  </footer>
                </article>
              </div>
            </article>

            <article class="vt-panel form-section">
              <header class="panel-header"><div><span class="vt-kicker">CANARY → PERCENTAGE</span><h2>Tạo rollout</h2><p>Canary luôn nhận trước. Phần trăm chỉ mở rộng sau khi canary report <code>active</code> đúng version.</p></div></header>
              <form class="form-stack" @submit.prevent="createFirmware">
                <VtField label="Firmware release" required><VtSelect v-model="firmwareForm.artifactId" required><option value="">Chọn release đã publish</option><option v-for="release in firmwareReleases.filter((item) => item.status === 'published')" :key="release.id" :value="release.id">{{ release.version }} · {{ release.channel }}</option></VtSelect></VtField>
                <VtField label="Thiết bị canary"><VtSelect v-model="firmwareForm.canaryDeviceId"><option value="">Không chọn (chỉ development)</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.firmwareVersion ?? "unknown" }}</option></VtSelect></VtField>
                <VtField label="Phần trăm fleet"><VtInput v-model.number="firmwareForm.percentage" type="number" min="0" max="100" required /></VtField>
                <p class="desired-note">Pause không đổi image đang chạy. Rollback chỉ đổi desired pointer về release đã publish trước đó; firmware vẫn tự verify chữ ký và image.</p>
                <VtButton type="submit" :busy="busyKey === 'firmware-create'" :disabled="!firmwareForm.artifactId">Bắt đầu rollout</VtButton>
              </form>
            </article>
          </div>
        </TabPanel>
      </TabPanels>
    </TabGroup>
  </section>
</template>
