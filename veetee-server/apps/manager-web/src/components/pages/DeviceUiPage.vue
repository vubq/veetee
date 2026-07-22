<script setup lang="ts">
import { computed, ref } from "vue";

import { deviceCapabilitiesSchema, type Artifact, type Device, type UiPackRollout } from "../../api/schemas";
import {
  DEVICE_UI_TARGET,
  FIRMWARE_SCREEN_COPY,
  FIRMWARE_STATE_IDS,
  FIRMWARE_THEMES,
  type FirmwareComposition,
  type FirmwareStateId,
} from "../../device-ui/firmware-contract";
import { formatBytes, formatDate, statusTone } from "../../utils/format";
import { normalizeRollouts } from "../../utils/rollouts";
import RolloutHistory from "../delivery/RolloutHistory.vue";
import FirmwareDisplayPreview from "../device-ui/FirmwareDisplayPreview.vue";
import { VtBadge, VtButton, VtEmptyState, VtIcon, VtPageHeader } from "../ui";

const props = defineProps<{
  devices: Device[];
  artifacts: Artifact[];
  rollouts: UiPackRollout[];
  selectedDeviceId: string;
  embedded?: boolean;
  stageUiPack: (file: File) => Promise<Artifact>;
  stageStandardUiPack: (theme: FirmwareComposition) => Promise<Artifact>;
  publishArtifact: (id: string) => Promise<void>;
  rolloutUiPack: (id: string) => Promise<void>;
}>();

const theme = ref<FirmwareComposition>("signal");
const previewState = ref<FirmwareStateId>("idle");
const selectedFile = ref<File>();
const stagedArtifact = ref<Artifact>();
const actionState = ref<"stage" | "publish" | "rollout" | "done">("stage");
const busy = ref(false);
const error = ref("");
const standardBusy = ref(false);
const standardResult = ref("");
const standardError = ref("");

const themes = FIRMWARE_THEMES;
const states = FIRMWARE_STATE_IDS.map((id) => ({ id, name: FIRMWARE_SCREEN_COPY[id].label }));
const currentTheme = computed(() => themes.find((item) => item.id === theme.value)!);
const currentCopy = computed(() => FIRMWARE_SCREEN_COPY[previewState.value]);
const currentPalette = computed(() => currentTheme.value.palette[previewState.value]);
const uiArtifacts = computed(() => props.artifacts.filter((artifact) => artifact.kind === "display_assets"));
const deviceRollouts = computed(() => normalizeRollouts(
  [],
  props.rollouts.filter((rollout) => rollout.deviceId === props.selectedDeviceId),
));
const selectedDevice = computed(() => props.devices.find((device) => device.id === props.selectedDeviceId));
const capabilities = computed(() => {
  const parsed = deviceCapabilitiesSchema.safeParse(selectedDevice.value?.reportedState.state.capabilities);
  return parsed.success ? parsed.data : undefined;
});
const displayCapability = computed(() => capabilities.value?.display);
const compatibilityIssue = computed(() => {
  const device = selectedDevice.value;
  const display = displayCapability.value;
  if (!device) return "Chưa chọn thiết bị.";
  if (device.status === "offline") return "Thiết bị đang offline.";
  if (!display) return "Thiết bị chưa report display capability.";
  if (capabilities.value?.board !== DEVICE_UI_TARGET.board) return `Board ${capabilities.value?.board} không tương thích.`;
  if (display.target !== DEVICE_UI_TARGET.display || display.width !== DEVICE_UI_TARGET.width || display.height !== DEVICE_UI_TARGET.height || display.colorFormat !== "rgb565") return `Màn hình ${display.target} không tương thích với pack ${DEVICE_UI_TARGET.display}.`;
  if (display.resourceAbi !== DEVICE_UI_TARGET.resourceAbi || display.uiAbi !== DEVICE_UI_TARGET.uiAbi) return `ABI thiết bị resource ${display.resourceAbi} / UI ${display.uiAbi} không tương thích.`;
  if (!display.hotReload) return "Firmware không hỗ trợ hot-reload UI Pack.";
  if (!display.compositions.includes(theme.value)) return `Firmware không hỗ trợ composition ${theme.value}.`;
  return "";
});
const canManageDisplay = computed(() => !compatibilityIssue.value);

async function applyStandardTheme(): Promise<void> {
  if (!canManageDisplay.value || !selectedDevice.value) {
    standardError.value = compatibilityIssue.value || "Thiết bị chưa sẵn sàng.";
    return;
  }
  standardBusy.value = true;
  standardError.value = "";
  standardResult.value = "";
  try {
    const artifact = await props.stageStandardUiPack(theme.value);
    await props.publishArtifact(artifact.id);
    await props.rolloutUiPack(artifact.id);
    standardResult.value = `Đã đặt ${currentTheme.value.name} làm desired UI cho ${selectedDevice.value.name}.`;
  } catch (exception) {
    standardError.value = exception instanceof Error ? exception.message : "Không thể áp dụng giao diện.";
  } finally {
    standardBusy.value = false;
  }
}

function chooseFile(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0];
  selectedFile.value = file;
  stagedArtifact.value = undefined;
  actionState.value = "stage";
  error.value = "";
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".vtp") && !file.name.toLowerCase().endsWith(".bin")) {
    error.value = "UI Pack cần dùng định dạng .vtp hoặc artifact .bin tương thích.";
  } else if (file.size > 2 * 1024 * 1024) {
    error.value = "UI Pack vượt giới hạn 2 MiB của UI ABI 1.";
  }
}

async function runArtifactAction(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    if (actionState.value === "stage") {
      if (!canManageDisplay.value) throw new Error(compatibilityIssue.value);
      if (!selectedFile.value) throw new Error("Hãy chọn UI Pack trước.");
      stagedArtifact.value = await props.stageUiPack(selectedFile.value);
      actionState.value = "publish";
    } else if (actionState.value === "publish") {
      if (!stagedArtifact.value) throw new Error("Artifact staging không còn khả dụng.");
      await props.publishArtifact(stagedArtifact.value.id);
      actionState.value = "rollout";
    } else if (actionState.value === "rollout") {
      if (!stagedArtifact.value) throw new Error("Artifact staging không còn khả dụng.");
      await props.rolloutUiPack(stagedArtifact.value.id);
      actionState.value = "done";
    }
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "Không thể xử lý UI Pack.";
  } finally {
    busy.value = false;
  }
}

const actionLabel = computed(() => ({
  stage: "Stage & kiểm tra pack", publish: "Publish UI Pack", rollout: "Rollout lên thiết bị", done: "Đã tạo rollout",
})[actionState.value]);
</script>

<template>
  <section class="vt-page" :class="{ 'is-embedded': embedded }" data-page="device-ui">
    <VtPageHeader v-if="!embedded" eyebrow="DISPLAY SYSTEM / UI PACK" title="Giao diện trên robot" description="Xem trước ba ngôn ngữ thiết kế tích hợp và phát hành UI Pack động mà không cần build lại toàn bộ firmware." />
    <header v-else class="device-subpage-header"><span class="vt-kicker">DISPLAY SYSTEM / UI PACK</span><h2>Giao diện của {{ selectedDevice?.name }}</h2><p>Preview đối ứng renderer FW và chỉ rollout sau khi capability thực tế của thiết bị khớp pack.</p></header>

    <div class="capability-gate" :class="{ ready: canManageDisplay }">
      <span><VtIcon :name="canManageDisplay ? 'check' : 'warning'" :size="20" /></span>
      <div><b>{{ canManageDisplay ? `${selectedDevice?.name} sẵn sàng nhận UI Pack` : "Chưa thể cập nhật giao diện" }}</b><p>{{ canManageDisplay ? `Capability lấy từ thiết bị: ${displayCapability?.target} · slot ${formatBytes(displayCapability?.slotBytes ?? 0)} · UI ABI ${displayCapability?.uiAbi}.` : compatibilityIssue }}</p></div>
      <VtBadge :tone="canManageDisplay ? 'success' : 'warning'" dot>{{ selectedDevice?.status ?? "no device" }}</VtBadge>
    </div>

    <div class="studio-layout">
      <aside class="studio-controls">
        <article class="vt-panel">
          <header class="panel-header"><div><span class="vt-kicker">BUILT-IN THEMES</span><h2>Phong cách</h2></div></header>
          <div class="theme-list">
            <button v-for="item in themes" :key="item.id" type="button" :class="{ active: theme === item.id }" :data-ui-theme="item.id" @click="theme = item.id">
              <span>{{ item.index }}</span><div><b>{{ item.name }}</b><small>{{ item.note }}</small></div><i></i>
            </button>
          </div>
        </article>
        <article class="vt-panel">
          <header class="panel-header"><div><span class="vt-kicker">CONVERSATION STATE</span><h2>Trạng thái</h2></div></header>
          <div class="state-picker firmware-states"><button v-for="item in states" :key="item.id" type="button" :class="{ active: previewState === item.id }" :data-ui-state="item.id" @click="previewState = item.id">{{ item.name }}</button></div>
        </article>
      </aside>

      <div class="device-preview-stage firmware-stage">
        <div class="preview-meta"><span><i></i> LIVE PREVIEW</span><b id="uiPreviewName">{{ currentTheme.index }} / {{ currentTheme.name }}</b></div>
        <div class="firmware-preview-body">
          <div class="display-shell firmware-shell" :data-theme="theme" :data-state="previewState" data-ui-preview>
            <FirmwareDisplayPreview :composition="currentTheme.composition" :state="previewState" :palette="currentPalette" activation-code="284716" />
          </div>
          <aside class="firmware-contract-card">
            <VtBadge tone="success" dot>Đối ứng renderer FW</VtBadge>
            <h3>{{ currentCopy.title }}</h3>
            <code>{{ previewState }} · enum {{ FIRMWARE_STATE_IDS.indexOf(previewState) }}</code>
            <dl>
              <div><dt>Device</dt><dd>{{ selectedDevice?.name ?? "Chưa chọn" }}</dd></div>
              <div><dt>Reported</dt><dd>{{ displayCapability?.target ?? "Chưa report" }}</dd></div>
              <div><dt>Composition</dt><dd>{{ currentTheme.composition }}</dd></div>
              <div><dt>Palette</dt><dd><i :style="{ background: currentPalette.background }"></i><i :style="{ background: currentPalette.foreground }"></i><i :style="{ background: currentPalette.accent }"></i></dd></div>
              <div><dt>ABI</dt><dd>resource {{ DEVICE_UI_TARGET.resourceAbi }} / UI {{ DEVICE_UI_TARGET.uiAbi }}</dd></div>
            </dl>
            <p>Canvas chạy cùng tọa độ 240×280, font bitmap 5×7, RGB565, state copy và ba composition trong `st7789_display.cpp`.</p>
            <VtButton data-apply-standard-theme :busy="standardBusy" :disabled="!canManageDisplay" @click="applyStandardTheme"><VtIcon name="display" :size="17" /> Áp dụng {{ currentTheme.name }}</VtButton>
            <small class="standard-pack-note">Server tạo VTPACK1 binary, ký manifest và rollout; FW chỉ tải composition, palette và assets tương ứng.</small>
            <small v-if="standardResult" class="desired-note" role="status">{{ standardResult }}</small>
            <small v-if="standardError" class="inline-error" role="alert">{{ standardError }}</small>
          </aside>
        </div>
        <p class="preview-disclaimer">Đây là software twin của renderer hiện tại. Màu, rotation, offset và độ sáng cuối cùng vẫn phải nghiệm thu trên ST7789 thật.</p>
      </div>
    </div>

    <div class="content-grid is-wide-left upload-section">
      <article class="vt-panel upload-panel">
        <header class="panel-header"><div><span class="vt-kicker">ADVANCED UI PACK INGEST</span><h2>Tải pack thủ công</h2><p>Chỉ dùng khi cần thử pack ngoài ba giao diện chuẩn. Manager API vẫn kiểm tra manifest, ABI, board, hash và chữ ký.</p></div><VtBadge tone="info">.VTP / .BIN</VtBadge></header>
        <label class="file-drop" :class="{ 'has-file': selectedFile, invalid: error }">
          <input type="file" accept=".vtp,.bin,application/octet-stream" data-ui-pack-file :disabled="!canManageDisplay" @change="chooseFile" />
          <span><VtIcon name="upload" :size="25" /></span>
          <div><b data-ui-file-name>{{ selectedFile?.name ?? "Kéo thả hoặc chọn UI Pack" }}</b><small>{{ selectedFile ? `${formatBytes(selectedFile.size)} · sẵn sàng gửi lên Manager API` : "Tối đa 2 MiB · VTPACK1 · UI ABI 1" }}</small></div>
          <VtBadge :tone="error ? 'danger' : selectedFile ? 'success' : 'neutral'" data-ui-upload-status>{{ error ? "Không hợp lệ" : selectedFile ? "Hợp lệ để staging" : "Chưa chọn file" }}</VtBadge>
        </label>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
        <div class="artifact-flow"><span data-step="1" :class="{ active: selectedFile }"><b>Kiểm tra file</b></span><i></i><span data-step="2" :class="{ active: stagedArtifact }"><b>Đã xác thực</b></span><i></i><span data-step="3" :class="{ active: ['rollout', 'done'].includes(actionState) }"><b>Đã publish</b></span><i></i><span data-step="4" :class="{ active: actionState === 'done' }"><b>Desired rollout</b></span></div>
        <VtButton data-ui-stage-pack :busy="busy" :disabled="!canManageDisplay || !selectedFile || Boolean(error) || actionState === 'done'" @click="runArtifactAction"><VtIcon :name="actionState === 'done' ? 'check' : 'arrow'" :size="17" /> {{ actionLabel }}</VtButton>
        <small v-if="actionState === 'done'" class="desired-note">Rollout mới chỉ cập nhật desired state; chờ firmware tải, xác minh và ACK trước khi coi là đã áp dụng.</small>
      </article>

      <article class="vt-panel">
        <header class="panel-header"><div><span class="vt-kicker">RELEASES</span><h2>UI Pack đã biết</h2><p>Catalog artifact và lịch sử delivery riêng của thiết bị đang chọn.</p></div></header>
        <div v-if="uiArtifacts.length" class="release-stack">
          <div v-for="artifact in uiArtifacts" :key="artifact.id"><span class="release-token">{{ artifact.version }}</span><div><b>{{ artifact.id }}</b><small>{{ formatBytes(artifact.sizeBytes) }} · {{ artifact.channel }} · {{ formatDate(artifact.createdAt) }}</small></div><VtBadge :tone="statusTone(artifact.status)">{{ artifact.status }}</VtBadge></div>
        </div>
        <VtEmptyState v-else icon="display" title="Chưa upload UI Pack" text="Signal vẫn là giao diện mặc định được nhúng trong firmware." />
        <div class="device-ui-rollouts"><span class="vt-kicker">DELIVERY HISTORY</span><RolloutHistory :rollouts="deviceRollouts" compact :show-kind="false" empty-title="Chưa rollout UI Pack" empty-text="Signal vẫn là failsafe cho tới khi thiết bị report một UI Pack đã verify và active." /></div>
      </article>
    </div>
  </section>
</template>
