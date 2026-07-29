<script setup lang="ts">
import { Tab, TabGroup, TabList, TabPanel, TabPanels } from "@headlessui/vue";
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import type {
  Agent,
  Artifact,
  AudioDiagnosticSession,
  ConversationEvent,
  Device,
  DeviceHealth,
  DeviceSelfTest,
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
import { VtBadge, VtButton, VtEmptyState, VtField, VtIcon, VtInput, VtMetricStrip, VtPageHeader, VtSelect } from "../ui";
import DeviceUiPage from "./DeviceUiPage.vue";
import DeviceDiagnosticsPanel from "../device-ui/DeviceDiagnosticsPanel.vue";
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
  assignDeviceAgent: (deviceId: string, agentId?: string) => Promise<void>;
  stageUiPack: (file: File) => Promise<Artifact>;
  stageStandardUiPack: (theme: FirmwareComposition) => Promise<Artifact>;
  publishArtifact: (id: string) => Promise<void>;
  rolloutUiPack: (id: string) => Promise<void>;
  rolloutWakeProfile: (id: string, deviceIds: string[]) => Promise<void>;
  callTool: (deviceId: string, name: string, argumentsValue: Record<string, unknown>, confirmed: boolean) => Promise<Record<string, unknown>>;
  getDiagnosticsHealth: (deviceId: string) => Promise<DeviceHealth>;
  startAudioDiagnostic: (deviceId: string, durationSeconds: number) => Promise<AudioDiagnosticSession>;
  runDeviceSelfTest: (deviceId: string) => Promise<DeviceSelfTest>;
}>();
const emit = defineEmits<{ select: [id: string]; openPair: [] }>();
const { t } = useI18n();

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
  if (agentAssignmentCurrent.value) return t("devices.agent.saved");
  if (selectedAgentId.value === (selected.value?.agentId ?? "") && selectedPublishedAgent.value) {
    return t("devices.agent.updateVersion", { version: selectedPublishedAgent.value.publishedVersion });
  }
  return t("devices.agent.save");
});
const delivery = computed(() => selected.value ? summarizeDeviceDelivery(selected.value) : undefined);
const presence = computed(() => selected.value ? devicePresence(selected.value) : undefined);
const fleetMetrics = computed(() => [
  {
    label: t("devices.metrics.online"),
    value: props.devices.filter((device) => devicePresence(device).state === "online").length,
    detail: t("devices.metrics.onlineDetail"),
    tone: "success" as const,
  },
  {
    label: t("devices.metrics.stale"),
    value: props.devices.filter((device) => devicePresence(device).state === "stale").length,
    detail: t("devices.metrics.staleDetail"),
    tone: "warning" as const,
  },
  {
    label: t("devices.metrics.showing"),
    value: `${filteredDevices.value.length}/${props.devices.length}`,
    detail: activeFilterCount.value ? t("devices.metrics.filtersActive", { count: activeFilterCount.value }) : t("devices.metrics.fullFleet"),
    tone: activeFilterCount.value ? "info" as const : "neutral" as const,
  },
]);

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

async function assignAgent(): Promise<void> {
  if (!selected.value) return;
  agentBusy.value = true;
  agentError.value = "";
  try {
    await props.assignDeviceAgent(selected.value.id, selectedAgentId.value || undefined);
  } catch (exception) {
    agentError.value = exception instanceof Error ? exception.message : t("devices.errors.assignAgent");
  } finally {
    agentBusy.value = false;
  }
}
</script>

<template>
  <section class="vt-page" data-page="devices">
    <VtPageHeader :eyebrow="t('pages.devices.eyebrow')" :title="t('pages.devices.title')" :description="t('pages.devices.description')">
      <template #actions><VtButton @click="emit('openPair')"><VtIcon name="plus" :size="17" /> {{ t("shell.pair") }}</VtButton></template>
    </VtPageHeader>

    <VtMetricStrip v-if="devices.length" class="fleet-metric-strip" :items="fleetMetrics" data-page-section="fleet-metrics" />

    <div v-if="devices.length" class="device-filter-panel" data-page-section="filters">
      <VtField :label="t('devices.filters.search')"><VtInput v-model="search" :placeholder="t('devices.filters.searchPlaceholder')" /></VtField>
      <VtField :label="t('devices.filters.status')"><VtSelect v-model="presenceFilter"><option value="all">{{ t("devices.filters.allStatuses") }}</option><option value="online">{{ t("devices.filters.online") }}</option><option value="idle">{{ t("devices.filters.idle") }}</option><option value="stale">{{ t("devices.filters.stale") }}</option><option value="offline">{{ t("devices.filters.offline") }}</option></VtSelect></VtField>
      <VtField :label="t('devices.filters.locale')"><VtSelect v-model="localeFilter"><option value="all">{{ t("devices.filters.allLocales") }}</option><option v-for="locale in localeOptions" :key="locale" :value="locale">{{ locale }}</option></VtSelect></VtField>
      <VtField :label="t('devices.filters.agent')"><VtSelect v-model="agentFilter"><option value="all">{{ t("devices.filters.allAgents") }}</option><option value="unassigned">{{ t("devices.filters.unassignedAgent") }}</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }}</option></VtSelect></VtField>
      <VtField :label="t('devices.filters.firmware')"><VtSelect v-model="firmwareFilter"><option value="all">{{ t("devices.filters.allFirmware") }}</option><option v-for="version in firmwareOptions" :key="version" :value="version">{{ version === "__missing__" ? t("devices.filters.notReported") : version }}</option></VtSelect></VtField>
      <button v-if="activeFilterCount" class="filter-reset" type="button" @click="resetFilters">{{ t("devices.filters.clear") }} <span>{{ activeFilterCount }}</span></button>
    </div>

    <div v-if="devices.length && filteredDevices.length" class="device-layout" data-page-section="primary-workspace">
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
            <p>{{ selected.hardwareId }} · {{ selected.firmwareVersion ? `Firmware ${selected.firmwareVersion}` : t("devices.details.noFirmware") }}</p>
            <div class="device-facts"><span><small>{{ t("devices.details.agent") }}</small><b>{{ agents.find((agent) => agent.id === selected?.agentId)?.name ?? t("devices.details.unassigned") }}</b></span><span><small>{{ t("devices.details.lastContact") }}</small><b>{{ formatDate(selected.lastSeenAt) }}</b></span><span><small>{{ t("devices.details.pairedAt") }}</small><b>{{ formatDate(selected.pairedAt) }}</b></span></div>
            <div class="device-agent-binding">
              <div class="device-agent-heading">
                <span class="device-agent-icon"><VtIcon name="agent" :size="20" /></span>
                <div>
                  <small>{{ t("devices.agent.eyebrow") }}</small>
                  <label :for="`device-agent-${selected.id}`">{{ t("devices.agent.title") }}</label>
                  <p>{{ t("devices.agent.description") }}</p>
                </div>
              </div>
              <div class="device-agent-controls">
                <span class="device-agent-select">
                  <VtSelect :id="`device-agent-${selected.id}`" v-model="selectedAgentId" :aria-label="t('devices.agent.ariaLabel')">
                    <option value="">{{ t("devices.agent.none") }}</option>
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
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="overview" :size="17" /> {{ t("devices.tabs.status") }}</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="display" :size="17" /> {{ t("devices.tabs.display") }}</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="mic" :size="17" /> {{ t("devices.tabs.wake") }}</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="tool" :size="17" /> {{ t("devices.tabs.mcp") }}</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="telemetry" :size="17" /> {{ t("devices.tabs.telemetry") }}</button></Tab>
            <Tab v-slot="{ selected: active }" as="template"><button :class="{ active }"><VtIcon name="mic" :size="17" /> {{ t("devices.tabs.diagnostics") }}</button></Tab>
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
            <TabPanel class="device-tab-panel">
              <DeviceDiagnosticsPanel :device-id="selected.id" :get-health="getDiagnosticsHealth" :start-audio="startAudioDiagnostic" :run-self-test="runDeviceSelfTest" />
            </TabPanel>
          </TabPanels>
        </TabGroup>
      </div>
    </div>

    <VtEmptyState v-else-if="!devices.length" icon="device" :title="t('devices.empty.title')" :text="t('devices.empty.body')">
      <VtButton @click="emit('openPair')"><VtIcon name="plus" :size="17" /> {{ t("devices.empty.pairFirst") }}</VtButton>
    </VtEmptyState>
    <VtEmptyState v-else icon="device" :title="t('devices.filteredEmpty.title')" :text="t('devices.filteredEmpty.body')"><VtButton size="sm" @click="resetFilters">{{ t("devices.filteredEmpty.clear") }}</VtButton></VtEmptyState>
  </section>
</template>
