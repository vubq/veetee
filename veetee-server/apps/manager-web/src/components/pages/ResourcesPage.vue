<script setup lang="ts">
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/vue";
import { computed, reactive, ref } from "vue";

import type { Artifact, Device, ResourceRollout, UiPackRollout, WakeProfile } from "../../api/schemas";
import { formatBytes, statusTone } from "../../utils/format";
import { normalizeRollouts } from "../../utils/rollouts";
import RolloutHistory from "../delivery/RolloutHistory.vue";
import { VtBadge, VtButton, VtEmptyState, VtField, VtIcon, VtInput, VtMetricStrip, VtOperationsHero, VtPageHeader, VtSelect } from "../ui";

const props = defineProps<{
  artifacts: Artifact[];
  wakeProfiles: WakeProfile[];
  rollouts: ResourceRollout[];
  uiPackRollouts: UiPackRollout[];
  devices: Device[];
  registerArtifact: (artifactId: string, license: string) => Promise<void>;
  publishArtifact: (id: string) => Promise<void>;
  createWakeProfile: (input: {
    artifactId: string; name: string; locale: string; channel: string; activationPhrase: string;
    activation: { detectorId: string; sensitivity: number; cooldownMs: number; allowedStates: string[] };
    interrupt: { detectorId: string; sensitivity: number; cooldownMs: number; allowedStates: string[] };
  }) => Promise<void>;
  publishWakeProfile: (id: string) => Promise<void>;
}>();

const artifactForm = reactive({ id: "", license: "" });
const wakeForm = reactive({ name: "Hey VeeTee", artifactId: "", phrase: "Hey VeeTee", channel: "development", locale: "vi-VN", activationDetector: "", interruptDetector: "" });
const busyKey = ref("");
const error = ref("");

const wakeArtifacts = computed(() => props.artifacts.filter((artifact) => ["resource_bundle", "model_pack"].includes(artifact.kind)));
const deliveryRollouts = computed(() => normalizeRollouts(props.rollouts, props.uiPackRollouts));
const activeRollouts = computed(() => deliveryRollouts.value.filter((rollout) => rollout.status === "active").length);
const completeRollouts = computed(() => deliveryRollouts.value.filter((rollout) => rollout.status === "complete").length);
const problemRollouts = computed(() => deliveryRollouts.value.filter((rollout) => ["failed", "rolled_back"].includes(rollout.status)).length);
const rolloutMetrics = computed(() => [
  { label: "Đang chờ thiết bị", value: activeRollouts.value, detail: "Desired đã tạo, chưa có ACK", tone: activeRollouts.value ? "warning" as const : "neutral" as const },
  { label: "Đã áp dụng", value: completeRollouts.value, detail: "Firmware report active", tone: "success" as const },
  { label: "Cần xử lý", value: problemRollouts.value, detail: "Thất bại hoặc đã rollback", tone: problemRollouts.value ? "danger" as const : "neutral" as const },
]);

async function perform(key: string, action: () => Promise<void>): Promise<void> {
  busyKey.value = key;
  error.value = "";
  try { await action(); }
  catch (exception) { error.value = exception instanceof Error ? exception.message : "Không thể thực hiện thao tác."; }
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
      activation: { detectorId: wakeForm.activationDetector.trim(), sensitivity: 0.55, cooldownMs: 1500, allowedStates: ["standby"] },
      interrupt: { detectorId: wakeForm.interruptDetector.trim(), sensitivity: 0.62, cooldownMs: 800, allowedStates: ["thinking", "speaking"] },
    });
  });
}

</script>

<template>
  <section class="vt-page" data-page="resources">
    <VtPageHeader eyebrow="SIGNED DELIVERY / RESOURCES" title="Model, wake word và OTA assets" description="Quản lý artifact có chữ ký, wake profile tách activation/interrupt và rollout có thể rollback theo desired state." />
    <p v-if="error" class="inline-error page-error" role="alert">{{ error }}</p>

    <TabGroup as="div" class="resource-tabs">
      <TabList class="vt-tabs">
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="resource" :size="17" /> Artifacts <span>{{ artifacts.length }}</span></button></Tab>
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="mic" :size="17" /> Wake profiles <span>{{ wakeProfiles.length }}</span></button></Tab>
        <Tab v-slot="{ selected }" as="template"><button :class="{ active: selected }"><VtIcon name="telemetry" :size="17" /> Rollouts <span>{{ deliveryRollouts.length }}</span></button></Tab>
      </TabList>

      <TabPanels>
        <TabPanel class="tab-panel">
          <div class="content-grid is-wide-left">
            <article class="vt-panel">
              <header class="panel-header"><div><span class="vt-kicker">ARTIFACT CATALOG</span><h2>Gói tài nguyên</h2></div></header>
              <div v-if="artifacts.length" class="artifact-list">
                <div v-for="artifact in artifacts" :key="artifact.id">
                  <span class="artifact-icon"><VtIcon :name="artifact.kind === 'display_assets' ? 'display' : 'resource'" :size="20" /></span>
                  <div><b>{{ artifact.id }}</b><small>{{ artifact.kind }} · {{ artifact.version }} · {{ formatBytes(artifact.sizeBytes) }}</small><code>{{ artifact.sha256.slice(0, 18) }}…</code></div>
                  <span><VtBadge :tone="statusTone(artifact.status)">{{ artifact.status }}</VtBadge><small>{{ artifact.channel }}</small></span>
                  <VtButton v-if="artifact.status === 'validated'" size="sm" variant="secondary" :busy="busyKey === `publish-${artifact.id}`" @click="perform(`publish-${artifact.id}`, () => publishArtifact(artifact.id))">Publish</VtButton>
                </div>
              </div>
              <VtEmptyState v-else icon="resource" title="Chưa có artifact" text="Đăng ký artifact đã được release pipeline tạo và ký." />
            </article>
            <article class="vt-panel form-section">
              <header class="panel-header"><div><span class="vt-kicker">REGISTER RELEASE</span><h2>Đăng ký artifact</h2><p>Manager không tạo binary; chỉ nhận artifact ID đã có trong storage cùng metadata release.</p></div></header>
              <form class="form-stack" @submit.prevent="register">
                <VtField label="Artifact ID" required><VtInput v-model="artifactForm.id" placeholder="wake-vi-1.0.0" required /></VtField>
                <VtField label="License / provenance" required><VtInput v-model="artifactForm.license" placeholder="Internal model pack · corpus approved" required /></VtField>
                <VtButton type="submit" :busy="busyKey === 'register'"><VtIcon name="plus" :size="16" /> Đăng ký</VtButton>
              </form>
            </article>
          </div>
        </TabPanel>

        <TabPanel class="tab-panel">
          <div class="content-grid is-wide-left">
            <article class="vt-panel">
              <header class="panel-header"><div><span class="vt-kicker">WAKE CATALOG</span><h2>Wake profile</h2></div></header>
              <div v-if="wakeProfiles.length" class="wake-list">
                <article v-for="profile in wakeProfiles" :key="profile.id">
                  <div class="wake-phrase"><span>“</span><b>{{ profile.activationPhrase }}</b><span>”</span></div>
                  <div class="wake-info"><h3>{{ profile.name }}</h3><p>{{ profile.locale }} · {{ profile.channel }} · artifact {{ profile.artifactId }}</p><div><VtBadge :tone="profile.productReady ? 'success' : 'warning'">{{ profile.productReady ? "Product-ready" : "Chưa benchmark" }}</VtBadge><VtBadge tone="info">Activation ≠ Interrupt</VtBadge></div></div>
                  <dl><div><dt>Activation</dt><dd>{{ profile.activation.detectorId }}</dd></div><div><dt>Interrupt</dt><dd>{{ profile.interrupt.detectorId }}</dd></div></dl>
                  <VtButton v-if="profile.publishedVersion === 0" size="sm" :busy="busyKey === `wake-publish-${profile.id}`" @click="perform(`wake-publish-${profile.id}`, () => publishWakeProfile(profile.id))">Publish v{{ profile.version }}</VtButton>
                  <VtBadge v-else tone="info">Áp dụng trong Thiết bị</VtBadge>
                </article>
              </div>
              <VtEmptyState v-else icon="mic" title="Chưa có wake profile" text="Tạo profile từ model pack đã ký; phrase text không tự sinh ra wake model." />
            </article>

            <article class="vt-panel form-section">
              <header class="panel-header"><div><span class="vt-kicker">NEW WAKE PROFILE</span><h2>Draft “Hey VeeTee”</h2><p>Hey VeeTee đọc là “hây vi ti”. Activation và interrupt dùng detector/policy riêng.</p></div></header>
              <form class="form-stack" @submit.prevent="createWake">
                <VtField label="Tên profile" required><VtInput v-model="wakeForm.name" required /></VtField>
                <VtField label="Model artifact" required><VtSelect v-model="wakeForm.artifactId" required><option value="">Chọn artifact</option><option v-for="artifact in wakeArtifacts" :key="artifact.id" :value="artifact.id">{{ artifact.id }} · {{ artifact.status }}</option></VtSelect></VtField>
                <div class="form-grid two"><VtField label="Activation phrase"><VtInput v-model="wakeForm.phrase" /></VtField><VtField label="Locale"><VtSelect v-model="wakeForm.locale"><option value="vi-VN">vi-VN</option><option value="en-US">en-US</option></VtSelect></VtField></div>
                <VtField label="Activation detector ID" required><VtInput v-model="wakeForm.activationDetector" placeholder="wakenet:hey_veetee_vi" required /></VtField>
                <VtField label="Interrupt detector ID" required><VtInput v-model="wakeForm.interruptDetector" placeholder="multinet:stop_vi" required /></VtField>
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
      </TabPanels>
    </TabGroup>
  </section>
</template>
