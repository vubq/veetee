<script setup lang="ts">
import { Dialog, DialogPanel, DialogTitle, Menu, MenuButton, MenuItem, MenuItems, Popover, PopoverButton, PopoverPanel, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, defineAsyncComponent, nextTick, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";

import { managerApi } from "../api/client";
import type { Artifact } from "../api/schemas";
import { canUseMemory as roleCanUseMemory } from "../auth/role-capabilities";
import { managerRoutes } from "../router";
import { useAuthStore } from "../stores/auth";
import type { AgentDraftInput, ManagerPage, ProviderUpdateInput, ToastItem } from "../types/manager";
import { preferredDevice } from "../utils/device-presence";
import PairDeviceDialog from "./PairDeviceDialog.vue";
import { VtBadge, VtBrandMark, VtButton, VtIcon, VtThemeSelector, VtToastRegion } from "./ui";

const AgentsPage = defineAsyncComponent(() => import("./pages/AgentsPage.vue"));
const DevicesPage = defineAsyncComponent(() => import("./pages/DevicesPage.vue"));
const OperationsPage = defineAsyncComponent(() => import("./pages/OperationsPage.vue"));
const OverviewPage = defineAsyncComponent(() => import("./pages/OverviewPage.vue"));
const ProvidersPage = defineAsyncComponent(() => import("./pages/ProvidersPage.vue"));
const RemoteMcpPage = defineAsyncComponent(() => import("./pages/RemoteMcpPage.vue"));
const MemoryPage = defineAsyncComponent(() => import("./pages/MemoryPage.vue"));
const RealtimeLabPage = defineAsyncComponent(() => import("./pages/RealtimeLabPage.vue"));
const ResourcesPage = defineAsyncComponent(() => import("./pages/ResourcesPage.vue"));

const auth = useAuthStore();
const queryClient = useQueryClient();
const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const activePage = computed(() => (route.name ?? "overview") as ManagerPage);
const activeRoute = computed(() => managerRoutes.find((item) => item.page === activePage.value) ?? managerRoutes[0]!);
const memoryAccessAllowed = computed(() => roleCanUseMemory(auth.principal?.role));
const visibleManagerRoutes = computed(() => managerRoutes.filter(
  (item) => item.page !== "memory" || memoryAccessAllowed.value,
));
const mobileMenuOpen = ref(false);
const mobileMenuButton = ref<HTMLButtonElement>();
const pairOpen = ref(false);
const selectedDeviceId = ref("");
const mainContent = ref<HTMLElement>();
const toasts = ref<ToastItem[]>([]);
let toastId = 0;

const onPage = (...pages: ManagerPage[]) => computed(() => pages.includes(activePage.value));
const health = useQuery({ queryKey: ["health"], queryFn: managerApi.health, retry: 1, refetchInterval: 15_000 });
const devices = useQuery({ queryKey: ["devices"], queryFn: managerApi.devices, enabled: computed(() => onPage("overview", "devices", "lab", "resources", "operations").value || (activePage.value === "memory" && memoryAccessAllowed.value)), refetchInterval: 15_000 });
const agents = useQuery({ queryKey: ["agents"], queryFn: managerApi.agents, enabled: computed(() => onPage("overview", "devices", "agents", "mcp", "lab").value || (activePage.value === "memory" && memoryAccessAllowed.value) || pairOpen.value) });
const agentPromptCatalog = useQuery({ queryKey: ["agent-prompt-catalog"], queryFn: managerApi.agentPromptCatalog, enabled: onPage("agents") });
const providers = useQuery({ queryKey: ["providers"], queryFn: managerApi.providers, enabled: onPage("overview", "agents", "providers") });
const baselineTools = useQuery({ queryKey: ["mcp-tools"], queryFn: managerApi.mcpTools, enabled: onPage("devices") });
const artifacts = useQuery({ queryKey: ["artifacts"], queryFn: managerApi.artifacts, enabled: onPage("devices", "resources") });
const wakeProfiles = useQuery({ queryKey: ["wake-profiles"], queryFn: managerApi.wakeProfiles, enabled: onPage("devices", "resources") });
const resourceRollouts = useQuery({ queryKey: ["resource-rollouts"], queryFn: managerApi.resourceRollouts, enabled: onPage("devices", "resources") });
const uiPackRollouts = useQuery({ queryKey: ["ui-pack-rollouts"], queryFn: managerApi.uiPackRollouts, enabled: onPage("devices", "resources") });
const firmwareReleases = useQuery({ queryKey: ["firmware-releases"], queryFn: managerApi.firmwareReleases, enabled: onPage("resources") });
const firmwareRollouts = useQuery({ queryKey: ["firmware-rollouts"], queryFn: managerApi.firmwareRollouts, enabled: onPage("resources") });
const fleetConversationEvents = useQuery({ queryKey: ["conversation-events", "fleet"], queryFn: () => managerApi.conversationEvents(undefined, 100), enabled: onPage("overview"), refetchInterval: 5_000, retry: false });
const operationsProfile = useQuery({ queryKey: ["operations-profile"], queryFn: managerApi.operationsProfile, enabled: onPage("operations") });
const auditEvents = useQuery({ queryKey: ["audit-events"], queryFn: () => managerApi.auditEvents({ limit: 150 }), enabled: onPage("operations"), refetchInterval: 15_000 });
const deviceTools = useQuery({ queryKey: computed(() => ["device-mcp-tools", selectedDeviceId.value]), queryFn: () => managerApi.deviceMcpTools(selectedDeviceId.value), enabled: computed(() => activePage.value === "devices" && Boolean(selectedDeviceId.value)), retry: false });
const deviceConversationEvents = useQuery({ queryKey: computed(() => ["conversation-events", "device", selectedDeviceId.value]), queryFn: () => managerApi.conversationEvents(selectedDeviceId.value), enabled: computed(() => activePage.value === "devices" && Boolean(selectedDeviceId.value)), refetchInterval: 1_500, retry: false });

const tools = computed(() => deviceTools.data.value ?? baselineTools.data.value ?? []);
const ready = computed(() => health.data.value?.status === "ready");
const apiHost = computed(() => { try { return new URL(managerApi.baseUrl).host; } catch { return managerApi.baseUrl; } });
const pageQueries = computed(() => {
  switch (activePage.value) {
    case "overview": return [devices, agents, providers, fleetConversationEvents];
    case "devices": return [devices, agents, baselineTools, artifacts, wakeProfiles, resourceRollouts, uiPackRollouts];
    case "agents": return [agents, providers, agentPromptCatalog];
    case "providers": return [providers];
    case "mcp": return [agents];
    case "memory": return memoryAccessAllowed.value ? [agents, devices] : [];
    case "lab": return [agents, devices];
    case "resources": return [artifacts, wakeProfiles, resourceRollouts, uiPackRollouts, firmwareReleases, firmwareRollouts, devices];
    case "operations": return [devices, operationsProfile, auditEvents];
  }
});
const hasQueryError = computed(() => pageQueries.value.some((query) => query.isError.value));
const initialDataReady = computed(() => pageQueries.value.every((query) => query.isFetched.value));

watch(
  () => devices.data.value,
  (list) => {
    if (!list?.length) { selectedDeviceId.value = ""; return; }
    if (!list.some((device) => device.id === selectedDeviceId.value)) selectedDeviceId.value = preferredDevice(list)?.id ?? "";
  },
  { immediate: true },
);

watch(
  () => route.fullPath,
  async () => {
    mobileMenuOpen.value = false;
    document.title = `${t(activeRoute.value.titleKey)} · Veetee`;
    await nextTick();
    mainContent.value?.focus({ preventScroll: true });
  },
  { immediate: true },
);

function toast(message: string, tone: ToastItem["tone"] = "success"): void {
  const item = { id: ++toastId, message, tone };
  toasts.value.push(item);
  window.setTimeout(() => dismissToast(item.id), 5_000);
}

watch(
  [activePage, memoryAccessAllowed],
  ([page, allowed]) => {
    if (page !== "memory" || allowed) return;
    toast(t("access.memoryRequiresOperator"), "danger");
    void router.replace({ name: "overview" });
  },
  { immediate: true },
);
function dismissToast(id: number): void { toasts.value = toasts.value.filter((item) => item.id !== id); }
function closeMobileMenu(): void {
  mobileMenuOpen.value = false;
  void nextTick(() => mobileMenuButton.value?.focus({ preventScroll: true }));
}
function navigate(page: ManagerPage): void { void router.push({ name: page }); mobileMenuOpen.value = false; }
async function refresh(...keys: string[]): Promise<void> {
  await Promise.all(keys.map(async (key) => {
    await queryClient.invalidateQueries({ queryKey: [key], refetchType: "none" });
    await queryClient.refetchQueries({ queryKey: [key], type: "active" });
  }));
}
async function pairDevice(code: string, name: string, agentId?: string): Promise<void> {
  await managerApi.claimPairing(code, name, agentId);
  await refresh("devices");
  toast(t("pairing.success", { name }));
}
async function assignDeviceAgent(deviceId: string, agentId?: string): Promise<void> {
  await managerApi.assignDeviceAgent(deviceId, agentId); await refresh("devices"); toast(t(agentId ? "toasts.deviceAgentAssigned" : "toasts.deviceAgentUnassigned"));
}
async function testProvider(id: string): Promise<void> { await managerApi.testProvider(id); await refresh("providers"); toast(t("toasts.providerTested")); }
async function updateProvider(id: string, input: ProviderUpdateInput): Promise<void> { await managerApi.updateProvider(id, input); await refresh("providers"); toast(t("toasts.providerSaved")); }
async function publishAgent(input: AgentDraftInput): Promise<void> {
  const current = agents.data.value?.find((agent) => agent.id === input.id);
  const currentConversation = current?.draftConfig.conversation;
  const nextConversation = input.draftConfig.conversation;
  const currentChains = Array.isArray(current?.draftConfig.providerChains) ? current.draftConfig.providerChains : [];
  const nextChains = Array.isArray(input.draftConfig.providerChains) ? input.draftConfig.providerChains : [];
  const replacedKeys = new Set(nextChains.flatMap((chain) => {
    if (!chain || typeof chain !== "object" || Array.isArray(chain)) return [];
    const value = chain as Record<string, unknown>;
    return typeof value.kind === "string" && typeof value.locale === "string" ? [`${value.kind}:${value.locale}`] : [];
  }));
  const providerChains = [...currentChains.filter((chain) => {
    if (!chain || typeof chain !== "object" || Array.isArray(chain)) return true;
    const value = chain as Record<string, unknown>;
    return !replacedKeys.has(`${String(value.kind)}:${String(value.locale)}`);
  }), ...nextChains];
  const draftConfig: Record<string, unknown> = { ...(current?.draftConfig ?? {}), ...input.draftConfig, providerChains, conversation: { ...(currentConversation && typeof currentConversation === "object" ? currentConversation : {}), ...(nextConversation && typeof nextConversation === "object" ? nextConversation : {}) } };
  if (Object.prototype.hasOwnProperty.call(input.draftConfig, "voice") && input.draftConfig.voice === undefined) {
    delete draftConfig.voice;
  }
  await managerApi.updateAgent(input.id, { name: input.name, defaultLocale: input.defaultLocale, interactionMode: input.interactionMode, persona: input.persona, draftConfig });
  await managerApi.publishAgent(input.id); await refresh("agents"); toast(t("toasts.agentPublished"));
}
async function createAgent(input: { name: string; defaultLocale: string; interactionMode: "auto" | "manual" | "realtime"; persona: string; draftConfig?: Record<string, unknown> }) { const agent = await managerApi.createAgent(input); await refresh("agents"); toast(t("toasts.agentCreated")); return agent; }
async function createPersonalityPreset(input: Parameters<typeof managerApi.createPersonalityPreset>[0]) { const preset = await managerApi.createPersonalityPreset(input); void refresh("agent-prompt-catalog"); toast(t("toasts.personalityCreated", { label: preset.label })); return preset; }
async function deletePersonalityPreset(id: string): Promise<void> { const preset = await managerApi.deletePersonalityPreset(id); void refresh("agent-prompt-catalog"); toast(t("toasts.personalityDeleted", { label: preset.label })); }
async function registerArtifact(id: string, license: string): Promise<void> { await managerApi.registerArtifact(id, license); await refresh("artifacts"); toast(t("toasts.artifactRegistered")); }
async function publishArtifact(id: string): Promise<void> { await managerApi.publishArtifact(id); await refresh("artifacts"); toast(t("toasts.artifactPublished")); }
async function stageUiPack(file: File): Promise<Artifact> { const artifact = await managerApi.stageUiPack(file); await refresh("artifacts"); toast(t("toasts.uiPackStaged")); return artifact; }
async function stageStandardUiPack(theme: "signal" | "monolith" | "quiet"): Promise<Artifact> { const artifact = await managerApi.stageStandardUiPack(theme); await refresh("artifacts"); toast(t("toasts.uiPackCreated", { theme })); return artifact; }
async function rolloutUiPack(id: string): Promise<void> { if (!selectedDeviceId.value) throw new Error(t("toasts.uiPackNoDevice")); await managerApi.rolloutUiPack(id, [selectedDeviceId.value]); await refresh("ui-pack-rollouts", "devices"); toast(t("toasts.uiPackRolloutCreated")); }
async function createWakeProfile(input: Parameters<typeof managerApi.createWakeProfile>[0]): Promise<void> { await managerApi.createWakeProfile(input); await refresh("wake-profiles"); toast(t("toasts.wakeProfileCreated")); }
async function publishWakeProfile(id: string): Promise<void> { await managerApi.publishWakeProfile(id); await refresh("wake-profiles"); toast(t("toasts.wakeProfilePublished")); }
async function rolloutWakeProfile(id: string, deviceIds: string[]): Promise<void> { await managerApi.rolloutWakeProfile(id, deviceIds); await refresh("resource-rollouts", "devices"); toast(t("toasts.wakeRolloutCreated")); }
async function publishFirmwareRelease(id: string): Promise<void> { await managerApi.publishFirmwareRelease(id); await refresh("firmware-releases", "artifacts"); toast(t("toasts.firmwarePublished")); }
async function createFirmwareRollout(artifactId: string, percentage: number, canaryDeviceIds: string[]): Promise<void> { await managerApi.createFirmwareRollout({ artifactId, percentage, canaryDeviceIds }); await refresh("firmware-rollouts", "devices"); toast(t("toasts.firmwareRolloutCreated")); }
async function pauseFirmwareRollout(id: string): Promise<void> { await managerApi.pauseFirmwareRollout(id); await refresh("firmware-rollouts"); toast(t("toasts.firmwareRolloutPaused")); }
async function resumeFirmwareRollout(id: string, percentage?: number): Promise<void> { await managerApi.resumeFirmwareRollout(id, percentage); await refresh("firmware-rollouts", "devices"); toast(t("toasts.firmwareRolloutResumed")); }
async function rollbackFirmwareRollout(id: string): Promise<void> { await managerApi.rollbackFirmwareRollout(id); await refresh("firmware-rollouts", "devices"); toast(t("toasts.firmwareRolledBack"), "danger"); }
async function callTool(deviceId: string, name: string, args: Record<string, unknown>, confirmed: boolean): Promise<Record<string, unknown>> { const result = await managerApi.callDeviceTool(deviceId, name, args, confirmed); toast(t("toasts.mcpToolResult", { name })); return result; }
function getDiagnosticsHealth(deviceId: string) { return managerApi.deviceDiagnosticsHealth(deviceId); }
async function startAudioDiagnostic(deviceId: string, durationSeconds: number) { const result = await managerApi.startDeviceAudioDiagnostic(deviceId, durationSeconds); toast(t("toasts.audioDiagnosticStarted", { seconds: durationSeconds })); return result; }
async function runDeviceSelfTest(deviceId: string) { const result = await managerApi.runDeviceSelfTest(deviceId); toast(t(result.overall === "pass" ? "toasts.selfTestPassed" : "toasts.selfTestNeedsAttention"), result.overall === "pass" ? "success" : "danger"); return result; }
</script>

<template>
  <div class="manager-app" :data-density="activeRoute.density">
    <a class="skip-link" href="#manager-content">{{ t("shell.skip") }}</a>
    <aside class="app-sidebar">
      <RouterLink class="brand-lockup" to="/overview" aria-label="Veetee Manager"><VtBrandMark /><span><b>veetee</b><small>{{ t("brand.operations") }}</small></span></RouterLink>
      <nav class="desktop-nav" :aria-label="t('nav.label')">
        <RouterLink v-for="item in visibleManagerRoutes" :key="item.page" :to="{ name: item.page }" :data-page-link="item.page"><span><VtIcon :name="item.icon" :size="19" /></span><span><b>{{ t(item.labelKey) }}</b><small>{{ t(item.shortKey) }}</small></span><i></i></RouterLink>
      </nav>
      <div class="sidebar-status"><span><i :class="{ ready }"></i><b>{{ ready ? t("shell.ready") : t("shell.degraded") }}</b></span><small>API · {{ apiHost }}</small></div>
    </aside>

    <div class="app-main">
      <header class="app-topbar">
        <button ref="mobileMenuButton" class="mobile-menu-button" type="button" :aria-label="t('nav.open')" aria-controls="mobile-navigation" :aria-expanded="mobileMenuOpen" @click="mobileMenuOpen = true"><VtIcon name="menu" :size="21" /></button>
        <div class="topbar-context"><span>{{ t(activeRoute.shortKey) }}</span><b>{{ t(activeRoute.labelKey) }}</b></div>
        <div class="topbar-actions">
          <VtButton size="sm" @click="pairOpen = true"><VtIcon name="plus" :size="16" /> <span class="button-label">{{ t("shell.pair") }}</span></VtButton>
          <Popover as="div" class="appearance-popover"><PopoverButton class="appearance-button" :aria-label="t('theme.label')"><VtIcon name="display" :size="18" /><span>{{ t("theme.label") }}</span></PopoverButton><Transition name="menu"><PopoverPanel class="appearance-panel"><VtThemeSelector /></PopoverPanel></Transition></Popover>
          <Menu as="div" class="profile-menu"><MenuButton class="profile-button"><span>{{ auth.principal?.displayName.slice(0, 1).toUpperCase() }}</span><div><b>{{ auth.principal?.displayName }}</b><small>{{ auth.principal?.role }}</small></div><VtIcon name="chevron" :size="15" /></MenuButton><Transition name="menu"><MenuItems class="profile-menu-items"><div class="profile-menu-identity"><b>{{ auth.principal?.displayName }}</b><small>{{ auth.principal?.email }}</small></div><MenuItem v-slot="{ active }"><button type="button" class="profile-logout" :class="{ active }" @click="auth.logout"><VtIcon name="logout" :size="17" /> {{ t("shell.logout") }}</button></MenuItem></MenuItems></Transition></Menu>
        </div>
      </header>

      <div v-if="hasQueryError" class="global-error"><VtIcon name="warning" :size="18" /><span><b>{{ t("shell.partialErrorTitle") }}</b> {{ t("shell.partialErrorBody") }}</span><button type="button" @click="refresh('devices', 'agents', 'providers', 'artifacts', 'wake-profiles', 'conversation-events', 'operations-profile', 'audit-events')"><VtIcon name="refresh" :size="16" /> {{ t("shell.retry") }}</button></div>

      <main id="manager-content" ref="mainContent" class="page-container" tabindex="-1" :aria-busy="!initialDataReady">
        <div v-if="!initialDataReady" class="page-loading" role="status" aria-live="polite"><VtBrandMark size="lg" /><div><b>{{ t("shell.loadingTitle") }}</b><small>{{ t("shell.loadingBody") }}</small></div></div>
        <RouterView v-else v-slot="{ route: viewRoute }">
          <OverviewPage v-if="viewRoute.name === 'overview'" :devices="devices.data.value ?? []" :agents="agents.data.value ?? []" :providers="providers.data.value ?? []" :events="fleetConversationEvents.data.value ?? []" :ready="ready" @navigate="navigate" @pair="pairOpen = true" />
          <DevicesPage v-else-if="viewRoute.name === 'devices'" :devices="devices.data.value ?? []" :agents="agents.data.value ?? []" :artifacts="artifacts.data.value ?? []" :wake-profiles="wakeProfiles.data.value ?? []" :resource-rollouts="resourceRollouts.data.value ?? []" :ui-pack-rollouts="uiPackRollouts.data.value ?? []" :tools="tools" :tools-live="Boolean(deviceTools.data.value)" :events="deviceConversationEvents.data.value ?? []" :selected-device-id="selectedDeviceId" :assign-device-agent="assignDeviceAgent" :stage-ui-pack="stageUiPack" :stage-standard-ui-pack="stageStandardUiPack" :publish-artifact="publishArtifact" :rollout-ui-pack="rolloutUiPack" :rollout-wake-profile="rolloutWakeProfile" :call-tool="callTool" :get-diagnostics-health="getDiagnosticsHealth" :start-audio-diagnostic="startAudioDiagnostic" :run-device-self-test="runDeviceSelfTest" @select="selectedDeviceId = $event" @open-pair="pairOpen = true" />
          <AgentsPage v-else-if="viewRoute.name === 'agents'" :agents="agents.data.value ?? []" :providers="providers.data.value ?? []" :prompt-catalog="agentPromptCatalog.data.value" :publish-agent="publishAgent" :create-agent="createAgent" :create-personality-preset="createPersonalityPreset" :delete-personality-preset="deletePersonalityPreset" />
          <ProvidersPage v-else-if="viewRoute.name === 'providers'" :providers="providers.data.value ?? []" :test-provider="testProvider" :update-provider="updateProvider" />
          <RemoteMcpPage v-else-if="viewRoute.name === 'mcp'" :agents="agents.data.value ?? []" :role="auth.principal?.role ?? 'VIEWER'" />
          <MemoryPage v-else-if="viewRoute.name === 'memory' && memoryAccessAllowed" :agents="agents.data.value ?? []" :devices="devices.data.value ?? []" />
          <RealtimeLabPage v-else-if="viewRoute.name === 'lab'" :agents="agents.data.value ?? []" :devices="devices.data.value ?? []" :create-session="managerApi.createLabSession" :toast="toast" />
          <ResourcesPage v-else-if="viewRoute.name === 'resources'" :artifacts="artifacts.data.value ?? []" :wake-profiles="wakeProfiles.data.value ?? []" :rollouts="resourceRollouts.data.value ?? []" :ui-pack-rollouts="uiPackRollouts.data.value ?? []" :firmware-releases="firmwareReleases.data.value ?? []" :firmware-rollouts="firmwareRollouts.data.value ?? []" :devices="devices.data.value ?? []" :register-artifact="registerArtifact" :publish-artifact="publishArtifact" :create-wake-profile="createWakeProfile" :publish-wake-profile="publishWakeProfile" :publish-firmware-release="publishFirmwareRelease" :create-firmware-rollout="createFirmwareRollout" :pause-firmware-rollout="pauseFirmwareRollout" :resume-firmware-rollout="resumeFirmwareRollout" :rollback-firmware-rollout="rollbackFirmwareRollout" />
          <OperationsPage v-else-if="viewRoute.name === 'operations'" :devices="devices.data.value ?? []" :audit-events="auditEvents.data.value ?? []" :profile="operationsProfile.data.value" :ready="ready" />
        </RouterView>
      </main>
    </div>

    <TransitionRoot :show="mobileMenuOpen" as="template"><Dialog class="mobile-nav-layer" @close="closeMobileMenu"><TransitionChild as="template" enter="dialog-backdrop-enter" enter-from="dialog-backdrop-from" enter-to="dialog-backdrop-to" leave="dialog-backdrop-leave" leave-from="dialog-backdrop-to" leave-to="dialog-backdrop-from"><div class="mobile-nav-backdrop"></div></TransitionChild><TransitionChild as="template" enter="drawer-enter" enter-from="drawer-from" enter-to="drawer-to" leave="drawer-leave" leave-from="drawer-to" leave-to="drawer-from" @after-leave="closeMobileMenu"><DialogPanel id="mobile-navigation" class="mobile-nav-panel"><DialogTitle class="sr-only">{{ t("nav.label") }}</DialogTitle><header><RouterLink class="brand-lockup" to="/overview" @click="closeMobileMenu"><VtBrandMark /><span><b>veetee</b><small>{{ t("brand.operations") }}</small></span></RouterLink><button class="vt-icon-button" type="button" :aria-label="t('nav.close')" @click="closeMobileMenu"><VtIcon name="close" :size="20" /></button></header><nav :aria-label="t('nav.label')"><RouterLink v-for="item in visibleManagerRoutes" :key="item.page" :to="{ name: item.page }" :data-page-link="item.page" @click="closeMobileMenu"><span><VtIcon :name="item.icon" :size="19" /></span><div><b>{{ t(item.labelKey) }}</b><small>{{ t(item.shortKey) }}</small></div></RouterLink></nav><footer><VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ ready ? t("shell.ready") : t("shell.degraded") }}</VtBadge><small>{{ apiHost }}</small></footer></DialogPanel></TransitionChild></Dialog></TransitionRoot>

    <PairDeviceDialog :open="pairOpen" :agents="agents.data.value ?? []" :pair-device="pairDevice" @close="pairOpen = false" />
    <VtToastRegion :items="toasts" @dismiss="dismissToast" />
  </div>
</template>
