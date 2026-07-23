<script setup lang="ts">
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/vue";
import { computed, ref, watch } from "vue";

import type {
  Agent,
  Artifact,
  ConversationEvent,
  Device,
  McpTool,
  ResourceRollout,
  UiPackRollout,
  WakeProfile,
} from "../../api/schemas";
import type { FirmwareComposition } from "../../device-ui/firmware-contract";
import { deliveryTone, summarizeDeviceDelivery } from "../../utils/device-delivery";
import { devicePresence, type DevicePresenceState } from "../../utils/device-presence";
import { formatDate, statusTone } from "../../utils/format";
import DesiredReportedSummary from "../device-ui/DesiredReportedSummary.vue";
import DeviceWakePanel from "../device-ui/DeviceWakePanel.vue";
import { VtBadge, VtButton, VtDialog, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect } from "../ui";
import DeviceUiPage from "./DeviceUiPage.vue";
import McpPage from "./McpPage.vue";
import TelemetryPage from "./TelemetryPage.vue";

const props = defineProps<{
  devices: Device[];
  agents: Agent[];
  artifacts: Artifact[];
  wakeProfiles: WakeProfile[];
  resourceRollouts: ResourceRollout[];
  uiPackRollouts: UiPackRollout[];
  tools: McpTool[];
  toolsLive: boolean;
  events: ConversationEvent[];
  selectedDeviceId: string;
  pairOpen: boolean;
  pairDevice: (code: string, name: string, agentId?: string) => Promise<void>;
  assignDeviceAgent: (deviceId: string, agentId?: string) => Promise<void>;
  stageUiPack: (file: File) => Promise<Artifact>;
  stageStandardUiPack: (theme: FirmwareComposition) => Promise<Artifact>;
  publishArtifact: (id: string) => Promise<void>;
  rolloutUiPack: (id: string) => Promise<void>;
  rolloutWakeProfile: (id: string, deviceIds: string[]) => Promise<void>;
  callTool: (deviceId: string, name: string, argumentsValue: Record<string, unknown>, confirmed: boolean) => Promise<Record<string, unknown>>;
}>();
const emit = defineEmits<{ select: [id: string]; closePair: []; openPair: [] }>();

const code = ref("");
const deviceName = ref("");
const agentId = ref("");
const busy = ref(false);
const error = ref("");
const agentBusy = ref(false);
const agentError = ref("");
const selectedAgentId = ref("");

const search = ref("");
const presenceFilter = ref<DevicePresenceState | "all">("all");
const localeFilter = ref("all");
const agentFilter = ref("all");
const firmwareFilter = ref("all");
const activeFilterCount = computed(() => [
  search.value.trim(),
  presenceFilter.value !== "all" ? presenceFilter.value : "",
  localeFilter.value !== "all" ? localeFilter.value : "",
  agentFilter.value !== "all" ? agentFilter.value : "",
  firmwareFilter.value !== "all" ? firmwareFilter.value : "",
].filter(Boolean).length);
const localeOptions = computed(() => [...new Set(props.agents.map((agent) => agent.defaultLocale))].sort());
const firmwareOptions = computed(() => [...new Set(props.devices.map((device) => device.firmwareVersion ?? "__missing__"))].sort());
const filteredDevices = computed(() => {
  const needle = search.value.trim().toLowerCase();
  return props.devices.filter((device) => {
    const agent = props.agents.find((candidate) => candidate.id === device.agentId);
    const matchesSearch = !needle || `${device.name} ${device.hardwareId} ${device.firmwareVersion ?? ""}`.toLowerCase().includes(needle);
    const matchesPresence = presenceFilter.value === "all" || devicePresence(device).state === presenceFilter.value;
    const matchesLocale = localeFilter.value === "all" || agent?.defaultLocale === localeFilter.value;
    const matchesAgent = agentFilter.value === "all" || (agentFilter.value === "unassigned" ? !device.agentId : device.agentId === agentFilter.value);
    const matchesFirmware = firmwareFilter.value === "all" || (device.firmwareVersion ?? "__missing__") === firmwareFilter.value;
    return matchesSearch && matchesPresence && matchesLocale && matchesAgent && matchesFirmware;
  });
});
const selected = computed(() => filteredDevices.value.find((device) => device.id === props.selectedDeviceId) ?? filteredDevices.value[0]);
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0));
const selectedPublishedAgent = computed(() => publishedAgents.value.find((agent) => agent.id === selectedAgentId.value));
const agentAssignmentCurrent = computed(() => {
  const device = selected.value;
  if (!device) return true;
  const assignedAgentId = device.agentId ?? "";
  if (selectedAgentId.value !== assignedAgentId) return false;
  if (!selectedAgentId.value) return true;
  const desired = device.desiredState.state;
  return desired.agentId === selectedAgentId.value
    && desired.agentConfigVersion === selectedPublishedAgent.value?.publishedVersion;
});
const agentSaveLabel = computed(() => {
  if (agentAssignmentCurrent.value) return "Đã lưu";
  if (selectedAgentId.value === (selected.value?.agentId ?? "") && selectedPublishedAgent.value) {
    return `Cập nhật v${selectedPublishedAgent.value.publishedVersion}`;
  }
  return "Lưu thay đổi";
});
const delivery = computed(() => selected.value ? summarizeDeviceDelivery(selected.value) : undefined);
const presence = computed(() => selected.value ? devicePresence(selected.value) : undefined);

watch(
  [filteredDevices, () => props.selectedDeviceId],
  ([list, selectedId]) => {
    if (list.length && !list.some((device) => device.id === selectedId)) emit("select", list[0]!.id);
  },
  { immediate: true },
);

watch(
  selected,
  (device) => {
    selectedAgentId.value = device?.agentId ?? "";
    agentError.value = "";
  },
  { immediate: true },
);

function resetFilters(): void {
  search.value = "";
  presenceFilter.value = "all";
  localeFilter.value = "all";
  agentFilter.value = "all";
  firmwareFilter.value = "all";
}

watch(
  () => props.pairOpen,
  (open) => {
    if (!open) return;
    code.value = "";
    deviceName.value = "";
    agentId.value = publishedAgents.value[0]?.id ?? "";
    error.value = "";
  },
);

function normalizeCode(): void {
  code.value = code.value.replace(/\D/g, "").slice(0, 6);
}

async function pair(): Promise<void> {
  normalizeCode();
  if (code.value.length !== 6) {
    error.value = "Mã ghép nối phải có đúng 6 chữ số.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await props.pairDevice(code.value, deviceName.value.trim() || `Veetee ${code.value.slice(-2)}`, agentId.value || undefined);
    emit("closePair");
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "Không thể ghép thiết bị.";
  } finally {
    busy.value = false;
  }
}

async function assignAgent(): Promise<void> {
  if (!selected.value) return;
  agentBusy.value = true;
  agentError.value = "";
  try {
    await props.assignDeviceAgent(selected.value.id, selectedAgentId.value || undefined);
  } catch (exception) {
    agentError.value = exception instanceof Error ? exception.message : "Không thể gán trợ lý.";
  } finally {
    agentBusy.value = false;
  }
}
</script>

<template>
  <section class="vt-page" data-page="devices">
    <VtPageHeader eyebrow="FLEET / THIẾT BỊ" title="Đội robot của bạn" description="Ghép nối bằng mã một lần, theo dõi firmware và kiểm tra desired/reported state mà không nhầm rollout với đã áp dụng." />

    <div v-if="devices.length" class="device-filter-panel">
      <VtField label="Tìm thiết bị"><VtInput v-model="search" placeholder="Tên, hardware ID, firmware…" /></VtField>
      <VtField label="Trạng thái"><VtSelect v-model="presenceFilter"><option value="all">Tất cả trạng thái</option><option value="online">Online mới</option><option value="idle">Đang rảnh</option><option value="stale">Dữ liệu cũ</option><option value="offline">Offline</option></VtSelect></VtField>
      <VtField label="Locale"><VtSelect v-model="localeFilter"><option value="all">Tất cả locale</option><option v-for="locale in localeOptions" :key="locale" :value="locale">{{ locale }}</option></VtSelect></VtField>
      <VtField label="Agent"><VtSelect v-model="agentFilter"><option value="all">Tất cả agent</option><option value="unassigned">Chưa gán agent</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option></VtSelect></VtField>
      <VtField label="Firmware"><VtSelect v-model="firmwareFilter"><option value="all">Tất cả firmware</option><option v-for="version in firmwareOptions" :key="version" :value="version">{{ version === "__missing__" ? "Chưa report" : version }}</option></VtSelect></VtField>
      <button v-if="activeFilterCount" class="filter-reset" type="button" @click="resetFilters">Xóa lọc <span>{{ activeFilterCount }}</span></button>
    </div>

    <div v-if="devices.length && filteredDevices.length" class="device-layout">
      <aside class="device-rail">
        <button v-for="device in filteredDevices" :key="device.id" type="button" :class="{ active: device.id === selected?.id }" @click="emit('select', device.id)">
          <span class="device-avatar"><VtIcon name="device" :size="22" /></span>
          <span><b>{{ device.name }}</b><small>{{ device.hardwareId }}</small></span>
          <i :class="devicePresence(device).state"></i>
        </button>
      </aside>

      <div v-if="selected" class="device-detail">
        <article class="device-identity-card">
          <div class="device-visual"><div class="mini-face"><i></i><i></i></div><span>{{ presence?.label.toUpperCase() }}</span></div>
          <div class="device-identity-copy">
            <div><VtBadge :tone="presence?.tone ?? statusTone(selected.status)" dot>{{ presence?.label ?? selected.status }}</VtBadge><VtBadge v-if="delivery && delivery.state !== 'unmanaged'" :tone="deliveryTone(delivery.state)">{{ delivery.title }}</VtBadge></div>
            <h2>{{ selected.name }}</h2>
            <p>{{ selected.hardwareId }} · {{ selected.firmwareVersion ? `Firmware ${selected.firmwareVersion}` : "Chưa báo firmware" }}</p>
            <div class="device-facts"><span><small>Agent</small><b>{{ agents.find((agent) => agent.id === selected?.agentId)?.name ?? "Chưa gán" }}</b></span><span><small>Liên hệ gần nhất</small><b>{{ formatDate(selected.lastSeenAt) }}</b></span><span><small>Đã ghép nối</small><b>{{ formatDate(selected.pairedAt) }}</b></span></div>
            <div class="device-agent-binding">
              <div class="device-agent-heading">
                <span class="device-agent-icon"><VtIcon name="agent" :size="20" /></span>
                <div>
                  <small>HỒ SƠ TRỢ LÝ</small>
                  <label :for="`device-agent-${selected.id}`">Trợ lý vận hành</label>
                  <p>Profile đã publish sẽ được đồng bộ xuống thiết bị.</p>
                </div>
              </div>
              <div class="device-agent-controls">
                <span class="device-agent-select">
                  <VtSelect :id="`device-agent-${selected.id}`" v-model="selectedAgentId" aria-label="Trợ lý cho thiết bị">
                    <option value="">Không gán trợ lý</option>
                    <option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option>
                  </VtSelect>
                  <VtIcon name="chevron" :size="16" />
                </span>
                <VtButton size="sm" :busy="agentBusy" :disabled="agentAssignmentCurrent" @click="assignAgent">
                  <VtIcon name="check" :size="15" />
                  {{ agentSaveLabel }}
                </VtButton>
              </div>
              <small v-if="agentError" class="inline-error" role="alert">{{ agentError }}</small>
            </div>
          </div>
        </article>

        <TabGroup as="div" class="device-workspace">
          <TabList class="vt-tabs device-tabs">
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="overview" :size="17" /> Trạng thái</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="display" :size="17" /> Display / UI</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="mic" :size="17" /> Wake word</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="tool" :size="17" /> MCP live</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="telemetry" :size="17" /> Telemetry</button></Tab>
          </TabList>
          <TabPanels>
            <TabPanel class="device-tab-panel">
              <DesiredReportedSummary :device="selected" />
            </TabPanel>
            <TabPanel class="device-tab-panel">
              <DeviceUiPage embedded :devices="[selected]" :artifacts="artifacts" :rollouts="uiPackRollouts" :selected-device-id="selected.id" :stage-ui-pack="stageUiPack" :stage-standard-ui-pack="stageStandardUiPack" :publish-artifact="publishArtifact" :rollout-ui-pack="rolloutUiPack" />
            </TabPanel>
            <TabPanel class="device-tab-panel">
              <DeviceWakePanel :device="selected" :artifacts="artifacts" :profiles="wakeProfiles" :rollouts="resourceRollouts" :rollout-wake-profile="rolloutWakeProfile" />
            </TabPanel>
            <TabPanel class="device-tab-panel">
              <McpPage embedded :devices="[selected]" :tools="tools" :tools-live="toolsLive" :selected-device-id="selected.id" :call-tool="callTool" @select-device="() => undefined" />
            </TabPanel>
            <TabPanel class="device-tab-panel">
              <TelemetryPage embedded :devices="[selected]" :events="events" :selected-device-id="selected.id" @select-device="() => undefined" />
            </TabPanel>
          </TabPanels>
        </TabGroup>
      </div>
    </div>

    <VtEmptyState v-else-if="!devices.length" icon="device" title="Chưa có thiết bị nào" text="Bật Veetee, lấy mã 6 số trên màn hình rồi ghép robot vào workspace.">
      <VtButton @click="emit('openPair')"><VtIcon name="plus" :size="17" /> Ghép thiết bị đầu tiên</VtButton>
    </VtEmptyState>
    <VtEmptyState v-else icon="device" title="Không có thiết bị phù hợp" text="Thử đổi bộ lọc hoặc xóa lọc để xem toàn bộ fleet."><VtButton size="sm" @click="resetFilters">Xóa bộ lọc</VtButton></VtEmptyState>

    <VtDialog :open="pairOpen" title="Ghép một Veetee mới" eyebrow="FLEET / SECURE PAIRING" icon="device" description="Mã chỉ dùng một lần và hết hạn theo policy của Manager API." width="sm" @close="emit('closePair')">
      <form id="device-pair-form" class="form-stack" @submit.prevent="pair">
        <div class="pair-dialog-note"><span><VtIcon name="device" :size="19" /></span><div><b>Robot phải đang ở màn hình pairing</b><p>Nhập đúng 6 chữ số đang hiển thị; mã sẽ bị vô hiệu ngay sau khi ghép thành công.</p></div></div>
        <VtField label="Mã ghép nối" hint="6 chữ số đang hiển thị trên màn hình robot" :error="error" required>
          <VtInput v-model="code" class="pair-code-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="284716" @input="normalizeCode" />
        </VtField>
        <VtField label="Tên thiết bị" hint="Có thể đổi lại sau">
          <VtInput v-model="deviceName" maxlength="80" placeholder="Veetee phòng khách" />
        </VtField>
        <VtField label="Agent mặc định">
          <VtSelect v-model="agentId"><option value="">Chưa gán assistant</option><option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option></VtSelect>
        </VtField>
      </form>
      <template #footer><VtButton variant="quiet" @click="emit('closePair')">Hủy</VtButton><VtButton form="device-pair-form" type="submit" :busy="busy" :disabled="code.length !== 6"><VtIcon name="plus" :size="17" /> Ghép thiết bị</VtButton></template>
    </VtDialog>
  </section>
</template>
