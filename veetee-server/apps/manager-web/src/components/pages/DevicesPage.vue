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
import { devicePresence } from "../../utils/device-presence";
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

const selected = computed(() => props.devices.find((device) => device.id === props.selectedDeviceId) ?? props.devices[0]);
const delivery = computed(() => selected.value ? summarizeDeviceDelivery(selected.value) : undefined);
const presence = computed(() => selected.value ? devicePresence(selected.value) : undefined);

watch(
  () => props.pairOpen,
  (open) => {
    if (!open) return;
    code.value = "";
    deviceName.value = "";
    agentId.value = props.agents[0]?.id ?? "";
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
</script>

<template>
  <section class="vt-page" data-page="devices">
    <VtPageHeader eyebrow="FLEET / THIẾT BỊ" title="Đội robot của bạn" description="Ghép nối bằng mã một lần, theo dõi firmware và kiểm tra desired/reported state mà không nhầm rollout với đã áp dụng." />

    <div v-if="devices.length" class="device-layout">
      <aside class="device-rail">
        <button v-for="device in devices" :key="device.id" type="button" :class="{ active: device.id === selected?.id }" @click="emit('select', device.id)">
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

    <VtEmptyState v-else icon="device" title="Chưa có thiết bị nào" text="Bật Veetee, lấy mã 6 số trên màn hình rồi ghép robot vào workspace.">
      <VtButton @click="emit('openPair')"><VtIcon name="plus" :size="17" /> Ghép thiết bị đầu tiên</VtButton>
    </VtEmptyState>

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
          <VtSelect v-model="agentId"><option value="">Chưa gán agent</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option></VtSelect>
        </VtField>
      </form>
      <template #footer><VtButton variant="quiet" @click="emit('closePair')">Hủy</VtButton><VtButton form="device-pair-form" type="submit" :busy="busy" :disabled="code.length !== 6"><VtIcon name="plus" :size="17" /> Ghép thiết bị</VtButton></template>
    </VtDialog>
  </section>
</template>
