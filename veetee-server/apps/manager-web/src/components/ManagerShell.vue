<script setup lang="ts">
import { Dialog, DialogPanel, Menu, MenuButton, MenuItem, MenuItems, TransitionChild, TransitionRoot } from "@headlessui/vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, ref, watch } from "vue";

import { managerApi } from "../api/client";
import type { Artifact } from "../api/schemas";
import { useAuthStore } from "../stores/auth";
import type { AgentDraftInput, ManagerPage, ProviderUpdateInput, ToastItem } from "../types/manager";
import type { VtIconName } from "./ui/VtIcon.vue";
import AgentsPage from "./pages/AgentsPage.vue";
import DevicesPage from "./pages/DevicesPage.vue";
import OverviewPage from "./pages/OverviewPage.vue";
import ProvidersPage from "./pages/ProvidersPage.vue";
import RealtimeLabPage from "./pages/RealtimeLabPage.vue";
import ResourcesPage from "./pages/ResourcesPage.vue";
import { VtBadge, VtButton, VtIcon, VtToastRegion } from "./ui";

const auth = useAuthStore();
const queryClient = useQueryClient();
const activePage = ref<ManagerPage>("overview");
const mobileMenuOpen = ref(false);
const pairOpen = ref(false);
const selectedDeviceId = ref("");
const toasts = ref<ToastItem[]>([]);
let toastId = 0;

const health = useQuery({ queryKey: ["health"], queryFn: managerApi.health, retry: 1, refetchInterval: 15_000 });
const devices = useQuery({ queryKey: ["devices"], queryFn: managerApi.devices, refetchInterval: 15_000 });
const agents = useQuery({ queryKey: ["agents"], queryFn: managerApi.agents });
const providers = useQuery({ queryKey: ["providers"], queryFn: managerApi.providers });
const baselineTools = useQuery({ queryKey: ["mcp-tools"], queryFn: managerApi.mcpTools });
const artifacts = useQuery({ queryKey: ["artifacts"], queryFn: managerApi.artifacts });
const wakeProfiles = useQuery({ queryKey: ["wake-profiles"], queryFn: managerApi.wakeProfiles });
const resourceRollouts = useQuery({ queryKey: ["resource-rollouts"], queryFn: managerApi.resourceRollouts });
const uiPackRollouts = useQuery({ queryKey: ["ui-pack-rollouts"], queryFn: managerApi.uiPackRollouts });
const deviceTools = useQuery({
  queryKey: computed(() => ["device-mcp-tools", selectedDeviceId.value]),
  queryFn: () => managerApi.deviceMcpTools(selectedDeviceId.value),
  enabled: computed(() => Boolean(selectedDeviceId.value)),
  retry: false,
});
const conversationEvents = useQuery({
  queryKey: computed(() => ["conversation-events", selectedDeviceId.value]),
  queryFn: () => managerApi.conversationEvents(selectedDeviceId.value),
  enabled: computed(() => Boolean(selectedDeviceId.value)),
  refetchInterval: 1_500,
  retry: false,
});

const navItems: Array<{ id: ManagerPage; label: string; short: string; icon: VtIconName }> = [
  { id: "overview", label: "Tổng quan", short: "Control room", icon: "overview" },
  { id: "devices", label: "Thiết bị", short: "Fleet", icon: "device" },
  { id: "agents", label: "Trợ lý", short: "Agents", icon: "agent" },
  { id: "providers", label: "Providers", short: "AI routing", icon: "provider" },
  { id: "lab", label: "Realtime Lab", short: "Voice simulator", icon: "lab" },
  { id: "resources", label: "Tài nguyên", short: "Wake & OTA", icon: "resource" },
];

const tools = computed(() => deviceTools.data.value ?? baselineTools.data.value ?? []);
const ready = computed(() => health.data.value?.status === "ready");
const hasQueryError = computed(() => [devices, agents, providers, artifacts, wakeProfiles].some((query) => query.isError.value));
const apiHost = computed(() => { try { return new URL(managerApi.baseUrl).host; } catch { return managerApi.baseUrl; } });

watch(
  () => devices.data.value,
  (list) => {
    if (!list?.length) { selectedDeviceId.value = ""; return; }
    if (!list.some((device) => device.id === selectedDeviceId.value)) selectedDeviceId.value = list[0]!.id;
  },
  { immediate: true },
);

function toast(message: string, tone: ToastItem["tone"] = "success"): void {
  const item = { id: ++toastId, message, tone };
  toasts.value.push(item);
  window.setTimeout(() => dismissToast(item.id), 5_000);
}

function dismissToast(id: number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id);
}

function navigate(page: ManagerPage): void {
  activePage.value = page;
  mobileMenuOpen.value = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function refresh(...keys: string[]): Promise<void> {
  await Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
}

async function pairDevice(code: string, name: string, agentId?: string): Promise<void> {
  await managerApi.claimPairing(code, name, agentId);
  await refresh("devices");
  toast(`Đã ghép ${name}.`);
}

async function testProvider(id: string): Promise<void> {
  await managerApi.testProvider(id);
  await refresh("providers");
  toast("Provider test hoàn tất.");
}

async function updateProvider(id: string, input: ProviderUpdateInput): Promise<void> {
  await managerApi.updateProvider(id, input);
  await refresh("providers");
  toast("Đã lưu cấu hình provider.");
}

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
  const providerChains = [
    ...currentChains.filter((chain) => {
      if (!chain || typeof chain !== "object" || Array.isArray(chain)) return true;
      const value = chain as Record<string, unknown>;
      return !replacedKeys.has(`${String(value.kind)}:${String(value.locale)}`);
    }),
    ...nextChains,
  ];
  await managerApi.updateAgent(input.id, {
    name: input.name, defaultLocale: input.defaultLocale, interactionMode: input.interactionMode, persona: input.persona,
    draftConfig: {
      ...(current?.draftConfig ?? {}), ...input.draftConfig, providerChains,
      conversation: {
        ...(currentConversation && typeof currentConversation === "object" ? currentConversation : {}),
        ...(nextConversation && typeof nextConversation === "object" ? nextConversation : {}),
      },
    },
  });
  await managerApi.publishAgent(input.id);
  await refresh("agents");
  toast("Agent version mới đã được publish.");
}

async function registerArtifact(id: string, license: string): Promise<void> {
  await managerApi.registerArtifact(id, license); await refresh("artifacts"); toast("Artifact đã được đăng ký.");
}
async function publishArtifact(id: string): Promise<void> {
  await managerApi.publishArtifact(id); await refresh("artifacts"); toast("Artifact đã được publish.");
}
async function stageUiPack(file: File): Promise<Artifact> {
  const artifact = await managerApi.stageUiPack(file); await refresh("artifacts"); toast("UI Pack đã qua staging."); return artifact;
}
async function stageStandardUiPack(theme: "signal" | "monolith" | "quiet"): Promise<Artifact> {
  const artifact = await managerApi.stageStandardUiPack(theme); await refresh("artifacts"); toast(`Đã tạo UI Pack ${theme}.`); return artifact;
}
async function rolloutUiPack(id: string): Promise<void> {
  if (!selectedDeviceId.value) throw new Error("Chưa có thiết bị để rollout UI Pack.");
  await managerApi.rolloutUiPack(id, [selectedDeviceId.value]); await refresh("ui-pack-rollouts", "devices"); toast("Đã tạo desired rollout cho UI Pack.");
}
async function createWakeProfile(input: Parameters<typeof managerApi.createWakeProfile>[0]): Promise<void> {
  await managerApi.createWakeProfile(input); await refresh("wake-profiles"); toast("Wake profile draft đã được tạo.");
}
async function publishWakeProfile(id: string): Promise<void> {
  await managerApi.publishWakeProfile(id); await refresh("wake-profiles"); toast("Wake profile đã được publish.");
}
async function rolloutWakeProfile(id: string, deviceIds: string[]): Promise<void> {
  await managerApi.rolloutWakeProfile(id, deviceIds); await refresh("resource-rollouts", "devices"); toast("Đã tạo desired rollout cho wake profile.");
}
async function callTool(deviceId: string, name: string, args: Record<string, unknown>, confirmed: boolean): Promise<Record<string, unknown>> {
  const result = await managerApi.callDeviceTool(deviceId, name, args, confirmed); toast(`MCP tool ${name} đã trả kết quả.`); return result;
}
</script>

<template>
  <div class="manager-app">
    <aside class="app-sidebar">
      <button class="brand-lockup" type="button" @click="navigate('overview')"><span class="brand-symbol"><i></i><i></i></span><span><b>veetee</b><small>robot operations</small></span></button>
      <nav class="desktop-nav" aria-label="Điều hướng chính">
        <button v-for="item in navItems" :key="item.id" type="button" :class="{ active: activePage === item.id }" :data-page-link="item.id" @click="navigate(item.id)">
          <span><VtIcon :name="item.icon" :size="19" /></span><span><b>{{ item.label }}</b><small>{{ item.short }}</small></span><i></i>
        </button>
      </nav>
      <div class="sidebar-status"><span><i :class="{ ready }"></i><b>{{ ready ? "System ready" : "System degraded" }}</b></span><small>API · {{ apiHost }}</small></div>
    </aside>

    <div class="app-main">
      <header class="app-topbar">
        <button class="mobile-menu-button" type="button" aria-label="Mở điều hướng" @click="mobileMenuOpen = true"><VtIcon name="menu" :size="21" /></button>
        <div class="topbar-context"><span>{{ navItems.find((item) => item.id === activePage)?.short }}</span><b>{{ navItems.find((item) => item.id === activePage)?.label }}</b></div>
        <div class="topbar-actions">
          <VtButton size="sm" @click="pairOpen = true"><VtIcon name="plus" :size="16" /> <span class="button-label">Ghép thiết bị</span></VtButton>
          <Menu as="div" class="profile-menu">
            <MenuButton class="profile-button"><span>{{ auth.principal?.displayName.slice(0, 1).toUpperCase() }}</span><div><b>{{ auth.principal?.displayName }}</b><small>{{ auth.principal?.role }}</small></div><VtIcon name="chevron" :size="15" /></MenuButton>
            <Transition name="menu">
              <MenuItems class="profile-menu-items">
                <div><b>{{ auth.principal?.displayName }}</b><small>{{ auth.principal?.email }}</small></div>
                <MenuItem v-slot="{ active }"><button type="button" :class="{ active }" @click="auth.logout"><VtIcon name="logout" :size="17" /> Đăng xuất</button></MenuItem>
              </MenuItems>
            </Transition>
          </Menu>
        </div>
      </header>

      <div v-if="hasQueryError" class="global-error"><VtIcon name="warning" :size="18" /><span><b>Một phần dữ liệu chưa tải được.</b> Kiểm tra Manager API rồi thử lại.</span><button type="button" @click="refresh('devices', 'agents', 'providers', 'artifacts', 'wake-profiles')"><VtIcon name="refresh" :size="16" /> Thử lại</button></div>

      <main class="page-container">
        <OverviewPage v-if="activePage === 'overview'" :devices="devices.data.value ?? []" :agents="agents.data.value ?? []" :providers="providers.data.value ?? []" :events="conversationEvents.data.value ?? []" :ready="ready" @navigate="navigate" @pair="pairOpen = true" />
        <DevicesPage v-else-if="activePage === 'devices'" :devices="devices.data.value ?? []" :agents="agents.data.value ?? []" :artifacts="artifacts.data.value ?? []" :wake-profiles="wakeProfiles.data.value ?? []" :resource-rollouts="resourceRollouts.data.value ?? []" :ui-pack-rollouts="uiPackRollouts.data.value ?? []" :tools="tools" :tools-live="Boolean(deviceTools.data.value)" :events="conversationEvents.data.value ?? []" :selected-device-id="selectedDeviceId" :pair-open="pairOpen" :pair-device="pairDevice" :stage-ui-pack="stageUiPack" :stage-standard-ui-pack="stageStandardUiPack" :publish-artifact="publishArtifact" :rollout-ui-pack="rolloutUiPack" :rollout-wake-profile="rolloutWakeProfile" :call-tool="callTool" @select="selectedDeviceId = $event" @open-pair="pairOpen = true" @close-pair="pairOpen = false" />
        <AgentsPage v-else-if="activePage === 'agents'" :agents="agents.data.value ?? []" :providers="providers.data.value ?? []" :publish-agent="publishAgent" />
        <ProvidersPage v-else-if="activePage === 'providers'" :providers="providers.data.value ?? []" :test-provider="testProvider" :update-provider="updateProvider" />
        <RealtimeLabPage v-else-if="activePage === 'lab'" :agents="agents.data.value ?? []" :devices="devices.data.value ?? []" :create-session="managerApi.createLabSession" :toast="toast" />
        <ResourcesPage v-else-if="activePage === 'resources'" :artifacts="artifacts.data.value ?? []" :wake-profiles="wakeProfiles.data.value ?? []" :rollouts="resourceRollouts.data.value ?? []" :ui-pack-rollouts="uiPackRollouts.data.value ?? []" :devices="devices.data.value ?? []" :register-artifact="registerArtifact" :publish-artifact="publishArtifact" :create-wake-profile="createWakeProfile" :publish-wake-profile="publishWakeProfile" />
        <OverviewPage v-else :devices="devices.data.value ?? []" :agents="agents.data.value ?? []" :providers="providers.data.value ?? []" :events="conversationEvents.data.value ?? []" :ready="ready" @navigate="navigate" @pair="pairOpen = true" />
      </main>
    </div>

    <TransitionRoot :show="mobileMenuOpen" as="template">
      <Dialog class="mobile-nav-layer" @close="mobileMenuOpen = false">
        <TransitionChild as="template" enter="dialog-backdrop-enter" enter-from="dialog-backdrop-from" enter-to="dialog-backdrop-to" leave="dialog-backdrop-leave" leave-from="dialog-backdrop-to" leave-to="dialog-backdrop-from"><div class="mobile-nav-backdrop"></div></TransitionChild>
        <TransitionChild as="template" enter="drawer-enter" enter-from="drawer-from" enter-to="drawer-to" leave="drawer-leave" leave-from="drawer-to" leave-to="drawer-from">
          <DialogPanel class="mobile-nav-panel"><header><button class="brand-lockup" type="button" @click="navigate('overview')"><span class="brand-symbol"><i></i><i></i></span><span><b>veetee</b><small>robot operations</small></span></button><button class="vt-icon-button" type="button" aria-label="Đóng" @click="mobileMenuOpen = false"><VtIcon name="close" :size="20" /></button></header><nav><button v-for="item in navItems" :key="item.id" type="button" :class="{ active: activePage === item.id }" @click="navigate(item.id)"><span><VtIcon :name="item.icon" :size="19" /></span><div><b>{{ item.label }}</b><small>{{ item.short }}</small></div></button></nav><footer><VtBadge :tone="ready ? 'success' : 'danger'" dot>{{ ready ? "System ready" : "System degraded" }}</VtBadge><small>{{ apiHost }}</small></footer></DialogPanel>
        </TransitionChild>
      </Dialog>
    </TransitionRoot>

    <DevicesPage v-if="activePage !== 'devices' && pairOpen" class="pair-only" :devices="[]" :agents="agents.data.value ?? []" :artifacts="artifacts.data.value ?? []" :wake-profiles="wakeProfiles.data.value ?? []" :resource-rollouts="resourceRollouts.data.value ?? []" :ui-pack-rollouts="uiPackRollouts.data.value ?? []" :tools="tools" :tools-live="false" :events="[]" selected-device-id="" :pair-open="pairOpen" :pair-device="pairDevice" :stage-ui-pack="stageUiPack" :stage-standard-ui-pack="stageStandardUiPack" :publish-artifact="publishArtifact" :rollout-ui-pack="rolloutUiPack" :rollout-wake-profile="rolloutWakeProfile" :call-tool="callTool" @select="() => undefined" @open-pair="pairOpen = true" @close-pair="pairOpen = false" />
    <VtToastRegion :items="toasts" @dismiss="dismissToast" />
  </div>
</template>
