<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watchEffect } from "vue";

import prototypePage from "../../../../prototypes/manager-web/index.html?raw";
import { managerApi } from "../api/client";
import { initializeRealtimeLab, type RealtimeLabController } from "../lab-controller";
import {
  initializePrototype,
  renderManagerView,
  type AgentDraftInput,
  type PrototypeController,
} from "../prototype-controller";
import { useAuthStore } from "../stores/auth";

const body = prototypePage.match(
  /<body>([\s\S]*?)<script src="app\.js"><\/script>[\s\S]*?<\/body>/,
)?.[1];
if (!body) throw new Error("Unable to load the approved Manager Web prototype");

const root = ref<HTMLElement>();
const controller = ref<PrototypeController>();
const labController = ref<RealtimeLabController>();
const auth = useAuthStore();
const queryClient = useQueryClient();

const health = useQuery({ queryKey: ["health"], queryFn: managerApi.health, retry: 1 });
const devices = useQuery({ queryKey: ["devices"], queryFn: managerApi.devices });
const agents = useQuery({ queryKey: ["agents"], queryFn: managerApi.agents });
const providers = useQuery({ queryKey: ["providers"], queryFn: managerApi.providers });
const baselineTools = useQuery({ queryKey: ["mcp-tools"], queryFn: managerApi.mcpTools });
const artifacts = useQuery({ queryKey: ["artifacts"], queryFn: managerApi.artifacts });
const wakeProfiles = useQuery({ queryKey: ["wake-profiles"], queryFn: managerApi.wakeProfiles });
const resourceRollouts = useQuery({
  queryKey: ["resource-rollouts"],
  queryFn: managerApi.resourceRollouts,
});
const uiPackRollouts = useQuery({
  queryKey: ["ui-pack-rollouts"],
  queryFn: managerApi.uiPackRollouts,
});
const activeDeviceId = computed(() => devices.data.value?.[0]?.id ?? "");
const deviceTools = useQuery({
  queryKey: computed(() => ["device-mcp-tools", activeDeviceId.value]),
  queryFn: () => managerApi.deviceMcpTools(activeDeviceId.value),
  enabled: computed(() => Boolean(activeDeviceId.value)),
  retry: false,
});
const conversationEvents = useQuery({
  queryKey: computed(() => ["conversation-events", activeDeviceId.value]),
  queryFn: () => managerApi.conversationEvents(activeDeviceId.value),
  enabled: computed(() => Boolean(activeDeviceId.value)),
  refetchInterval: 1_500,
  retry: false,
});
const tools = computed(() => deviceTools.data.value ?? baselineTools.data.value ?? []);

const apiHost = computed(() => {
  try {
    return new URL(managerApi.baseUrl).host;
  } catch {
    return managerApi.baseUrl;
  }
});

async function refresh(key: string): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: [key] });
}

async function pair(code: string, name: string, agentId?: string): Promise<void> {
  await managerApi.claimPairing(code, name, agentId);
  await refresh("devices");
}

async function testProvider(providerId: string): Promise<void> {
  await managerApi.testProvider(providerId);
  await refresh("providers");
}

async function updateProvider(
  providerId: string,
  input: Parameters<typeof managerApi.updateProvider>[1],
): Promise<void> {
  await managerApi.updateProvider(providerId, input);
  await refresh("providers");
}

async function publishAgent(input: AgentDraftInput): Promise<void> {
  const current = agents.data.value?.find((agent) => agent.id === input.id);
  const currentConversation = current?.draftConfig.conversation;
  const nextConversation = input.draftConfig.conversation;
  const currentProviderChains = Array.isArray(current?.draftConfig.providerChains)
    ? current.draftConfig.providerChains
    : [];
  const nextProviderChains = Array.isArray(input.draftConfig.providerChains)
    ? input.draftConfig.providerChains
    : [];
  const replacedChainKeys = new Set(
    nextProviderChains.flatMap((chain) => {
      if (!chain || typeof chain !== "object" || Array.isArray(chain)) return [];
      const value = chain as Record<string, unknown>;
      return typeof value.kind === "string" && typeof value.locale === "string"
        ? [`${value.kind}:${value.locale}`]
        : [];
    }),
  );
  const providerChains = [
    ...currentProviderChains.filter((chain) => {
      if (!chain || typeof chain !== "object" || Array.isArray(chain)) return true;
      const value = chain as Record<string, unknown>;
      return !replacedChainKeys.has(`${String(value.kind)}:${String(value.locale)}`);
    }),
    ...nextProviderChains,
  ];
  await managerApi.updateAgent(input.id, {
    name: input.name,
    defaultLocale: input.defaultLocale,
    interactionMode: input.interactionMode,
    persona: input.persona,
    draftConfig: {
      ...(current?.draftConfig ?? {}),
      ...input.draftConfig,
      providerChains,
      conversation: {
        ...(currentConversation && typeof currentConversation === "object"
          ? currentConversation
          : {}),
        ...(nextConversation && typeof nextConversation === "object" ? nextConversation : {}),
      },
    },
  });
  await managerApi.publishAgent(input.id);
  await refresh("agents");
}

async function callTool(
  deviceId: string,
  name: string,
  argumentsValue: Record<string, unknown>,
  confirmed: boolean,
): Promise<Record<string, unknown>> {
  return managerApi.callDeviceTool(deviceId, name, argumentsValue, confirmed);
}

async function registerArtifact(artifactId: string, license: string): Promise<void> {
  await managerApi.registerArtifact(artifactId, license);
  await refresh("artifacts");
}

async function publishArtifact(id: string): Promise<void> {
  await managerApi.publishArtifact(id);
  await refresh("artifacts");
}

async function stageUiPack(file: File) {
  const artifact = await managerApi.stageUiPack(file);
  await refresh("artifacts");
  return artifact;
}

async function rolloutUiPack(id: string): Promise<void> {
  if (!activeDeviceId.value) throw new Error("Chưa có thiết bị để rollout UI Pack.");
  await managerApi.rolloutUiPack(id, [activeDeviceId.value]);
  await Promise.all([refresh("ui-pack-rollouts"), refresh("devices")]);
}

async function createWakeProfile(input: Parameters<typeof managerApi.createWakeProfile>[0]) {
  await managerApi.createWakeProfile(input);
  await refresh("wake-profiles");
}

async function publishWakeProfile(id: string): Promise<void> {
  await managerApi.publishWakeProfile(id);
  await refresh("wake-profiles");
}

async function rolloutWakeProfile(id: string, deviceIds: string[]): Promise<void> {
  await managerApi.rolloutWakeProfile(id, deviceIds);
  await Promise.all([refresh("resource-rollouts"), refresh("devices")]);
}

onMounted(async () => {
  await nextTick();
  if (!root.value) return;
  controller.value = initializePrototype(root.value, {
    pair,
    testProvider,
    updateProvider,
    publishAgent,
    callTool,
    registerArtifact,
    publishArtifact,
    stageUiPack,
    rolloutUiPack,
    createWakeProfile,
    publishWakeProfile,
    rolloutWakeProfile,
    logout: () => auth.logout(),
  });
  labController.value = initializeRealtimeLab(root.value, {
    createSession: managerApi.createLabSession,
    toast: (message) => controller.value?.toast(message),
  });
  labController.value.updateCatalog(agents.data.value ?? [], devices.data.value ?? []);
});

watchEffect(() => {
  if (!root.value || !auth.principal) return;
  renderManagerView(root.value, {
    principal: auth.principal,
    devices: devices.data.value ?? [],
    agents: agents.data.value ?? [],
    providers: providers.data.value ?? [],
    tools: tools.value,
    conversationEvents: conversationEvents.data.value ?? [],
    artifacts: artifacts.data.value ?? [],
    wakeProfiles: wakeProfiles.data.value ?? [],
    resourceRollouts: resourceRollouts.data.value ?? [],
    activeDeviceId: activeDeviceId.value || undefined,
    toolsLive: Boolean(deviceTools.data.value),
    apiHost: apiHost.value,
    ready: health.data.value?.status === "ready",
  });
  labController.value?.updateCatalog(agents.data.value ?? [], devices.data.value ?? []);
});

onBeforeUnmount(() => {
  labController.value?.destroy();
  controller.value?.destroy();
});
</script>

<template>
  <div ref="root" v-html="body"></div>
</template>
