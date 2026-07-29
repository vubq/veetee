<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { managerApi } from "../../api/client";
import type { Agent, RemoteMcpEndpoint, RemoteMcpToolPolicy } from "../../api/schemas";
import {
  canManageRemoteMcp,
  canTestRemoteMcp,
  type TenantRole,
} from "../../auth/role-capabilities";
import {
  VtBadge,
  VtButton,
  VtDialog,
  VtEmptyState,
  VtField,
  VtIcon,
  VtInput,
  VtPageHeader,
  VtSelect,
} from "../ui";

const props = defineProps<{ agents: Agent[]; role: TenantRole }>();
const { locale, t } = useI18n();
const queryClient = useQueryClient();
const selectedAgentId = ref("");
const selectedEndpointId = ref("");
const createOpen = ref(false);
const secretOpen = ref(false);
const busy = ref(false);
const testBusy = ref("");
const retryBusy = ref(false);
const error = ref("");
const secretAction = ref<"rotate" | "clear">("rotate");
const secretValue = ref("");

interface AssignmentDraft {
  selected: boolean;
  timeoutSeconds: number;
  toolNames: string[];
}

interface ToolDraft extends RemoteMcpToolPolicy {
  id: number;
}

let toolDraftId = 1;
const assignments = reactive<Record<string, AssignmentDraft>>({});
const form = reactive({
  name: "",
  url: "",
  transport: "streamable_http" as "streamable_http" | "sse",
  authType: "none" as "none" | "bearer" | "header",
  authHeaderName: "X-API-Key",
  secret: "",
  timeoutSeconds: 10,
  resultMaxBytes: 65_536,
  networkPolicy: "public_only" as "public_only" | "private_allowlist",
  tools: [newToolDraft()] as ToolDraft[],
});

const endpointsQuery = useQuery({
  queryKey: ["remote-mcp-endpoints"],
  queryFn: managerApi.remoteMcpEndpoints,
  retry: 1,
});
const assignmentsQuery = useQuery({
  queryKey: computed(() => ["remote-mcp-assignments", selectedAgentId.value]),
  queryFn: () => managerApi.agentRemoteMcpAssignments(selectedAgentId.value),
  enabled: computed(() => Boolean(selectedAgentId.value)),
  retry: 1,
});

const endpoints = computed(() => endpointsQuery.data.value ?? []);
const canManage = computed(() => canManageRemoteMcp(props.role));
const canTest = computed(() => canTestRemoteMcp(props.role));
const selectedEndpoint = computed(
  () => endpoints.value.find((endpoint) => endpoint.id === selectedEndpointId.value) ?? endpoints.value[0],
);
const endpointsLoading = computed(() => endpointsQuery.isPending.value);
const assignmentsLoading = computed(() => assignmentsQuery.isPending.value && Boolean(selectedAgentId.value));
const queryError = computed(() => {
  const value = endpointsQuery.error.value ?? assignmentsQuery.error.value;
  return value instanceof Error ? value.message : value ? t("remoteMcp.errors.loadFailed") : "";
});
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0));
const healthyCount = computed(() => endpoints.value.filter((endpoint) => endpoint.health === "healthy").length);
const endpointHostname = computed(() => {
  try {
    return new URL(form.url).hostname.toLowerCase();
  } catch {
    return "";
  }
});

watch(
  publishedAgents,
  (agents) => {
    if (!agents.some((agent) => agent.id === selectedAgentId.value)) {
      selectedAgentId.value = agents[0]?.id ?? "";
    }
  },
  { immediate: true },
);

watch(
  endpoints,
  (items) => {
    if (!items.some((endpoint) => endpoint.id === selectedEndpointId.value)) {
      selectedEndpointId.value = items[0]?.id ?? "";
    }
  },
  { immediate: true },
);

watch(
  [endpoints, () => assignmentsQuery.data.value],
  ([endpointItems, assignmentPage]) => {
    const existing = new Map((assignmentPage?.items ?? []).map((assignment) => [assignment.endpointId, assignment]));
    for (const key of Object.keys(assignments)) delete assignments[key];
    for (const endpoint of endpointItems) {
      const assignment = existing.get(endpoint.id);
      assignments[endpoint.id] = {
        selected: Boolean(assignment),
        timeoutSeconds: assignment?.timeoutSeconds ?? endpoint.timeoutSeconds,
        toolNames: assignment?.toolNames ?? [],
      };
    }
  },
  { immediate: true },
);

function newToolDraft(): ToolDraft {
  return {
    id: toolDraftId++,
    name: "",
    safetyClass: "read_only",
    requiresConfirmation: false,
  };
}

function resetCreate(): void {
  form.name = "";
  form.url = "";
  form.transport = "streamable_http";
  form.authType = "none";
  form.authHeaderName = "X-API-Key";
  form.secret = "";
  form.timeoutSeconds = 10;
  form.resultMaxBytes = 65_536;
  form.networkPolicy = "public_only";
  form.tools.splice(0, form.tools.length, newToolDraft());
  error.value = "";
}

function openCreate(): void {
  if (!canManage.value) return;
  resetCreate();
  createOpen.value = true;
}

function closeCreate(): void {
  form.secret = "";
  error.value = "";
  createOpen.value = false;
}

function closeSecret(): void {
  secretValue.value = "";
  error.value = "";
  secretOpen.value = false;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(locale.value).format(value);
}

function healthLabel(endpoint: RemoteMcpEndpoint): string {
  return t(`remoteMcp.health.${endpoint.enabled ? endpoint.health : "disabled"}`);
}

async function retryQueries(): Promise<void> {
  retryBusy.value = true;
  error.value = "";
  try {
    await Promise.all([
      endpointsQuery.refetch(),
      ...(selectedAgentId.value ? [assignmentsQuery.refetch()] : []),
    ]);
  } finally {
    retryBusy.value = false;
  }
}

function endpointTone(endpoint: RemoteMcpEndpoint): "success" | "warning" | "danger" | "neutral" {
  if (!endpoint.enabled) return "neutral";
  if (endpoint.health === "healthy") return "success";
  if (endpoint.health === "degraded") return "danger";
  return "warning";
}

async function createEndpoint(): Promise<void> {
  if (!canManage.value) return;
  const tools = form.tools
    .map(({ id: _id, ...tool }) => ({ ...tool, name: tool.name.trim() }))
    .filter((tool) => tool.name);
  if (!form.name.trim() || !form.url.trim() || !tools.length) {
    error.value = t("remoteMcp.errors.required");
    return;
  }
  if (new Set(tools.map((tool) => tool.name)).size !== tools.length) {
    error.value = t("remoteMcp.errors.duplicateTool");
    return;
  }
  if (tools.some((tool) => ["disruptive", "destructive"].includes(tool.safetyClass) && !tool.requiresConfirmation)) {
    error.value = t("remoteMcp.errors.unsafeToolConfirmation");
    return;
  }
  if (form.authType !== "none" && !form.secret.trim()) {
    error.value = t("remoteMcp.errors.secretRequired");
    return;
  }
  if (form.authType === "header" && !form.authHeaderName.trim()) {
    error.value = t("remoteMcp.errors.headerRequired");
    return;
  }
  if (!endpointHostname.value) {
    error.value = t("remoteMcp.errors.allowlistRequired");
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const endpoint = await managerApi.createRemoteMcpEndpoint({
      name: form.name.trim(),
      url: form.url.trim(),
      transport: form.transport,
      authType: form.authType,
      ...(form.authType === "header" ? { authHeaderName: form.authHeaderName.trim() } : {}),
      ...(form.authType !== "none" ? { secret: form.secret } : {}),
      timeoutSeconds: Number(form.timeoutSeconds),
      resultMaxBytes: Number(form.resultMaxBytes),
      networkPolicy: form.networkPolicy,
      allowedHosts: [endpointHostname.value],
      tools,
    });
    form.secret = "";
    await queryClient.invalidateQueries({ queryKey: ["remote-mcp-endpoints"] });
    selectedEndpointId.value = endpoint.id;
    closeCreate();
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("remoteMcp.errors.createFailed");
  } finally {
    busy.value = false;
  }
}

async function testEndpoint(endpoint: RemoteMcpEndpoint): Promise<void> {
  if (!canTest.value) return;
  testBusy.value = endpoint.id;
  error.value = "";
  try {
    await managerApi.testRemoteMcpEndpoint(endpoint.id);
    await queryClient.invalidateQueries({ queryKey: ["remote-mcp-endpoints"] });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("remoteMcp.errors.testFailed");
  } finally {
    testBusy.value = "";
  }
}

async function toggleEndpoint(endpoint: RemoteMcpEndpoint): Promise<void> {
  if (!canManage.value) return;
  busy.value = true;
  error.value = "";
  try {
    await managerApi.updateRemoteMcpEndpoint(endpoint.id, {
      enabled: !endpoint.enabled,
      secretAction: "keep",
    });
    await queryClient.invalidateQueries({ queryKey: ["remote-mcp-endpoints"] });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("remoteMcp.errors.updateFailed");
  } finally {
    busy.value = false;
  }
}

function openSecret(endpoint: RemoteMcpEndpoint): void {
  if (!canManage.value) return;
  selectedEndpointId.value = endpoint.id;
  secretAction.value = "rotate";
  secretValue.value = "";
  error.value = "";
  secretOpen.value = true;
}

async function updateSecret(): Promise<void> {
  if (!canManage.value) return;
  const endpoint = selectedEndpoint.value;
  if (!endpoint) return;
  if (secretAction.value === "rotate" && !secretValue.value) {
    error.value = t("remoteMcp.errors.secretRequired");
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await managerApi.updateRemoteMcpEndpoint(endpoint.id, {
      secretAction: secretAction.value,
      ...(secretAction.value === "rotate" ? { secret: secretValue.value } : {}),
    });
    secretValue.value = "";
    await queryClient.invalidateQueries({ queryKey: ["remote-mcp-endpoints"] });
    closeSecret();
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("remoteMcp.errors.updateFailed");
  } finally {
    busy.value = false;
  }
}

function setTool(endpointId: string, toolName: string, checked: boolean): void {
  const draft = assignments[endpointId];
  if (!draft) return;
  draft.toolNames = checked
    ? [...new Set([...draft.toolNames, toolName])]
    : draft.toolNames.filter((name) => name !== toolName);
}

async function saveAssignments(): Promise<void> {
  if (!canManage.value) return;
  if (!selectedAgentId.value) return;
  const selected = endpoints.value
    .filter((endpoint) => assignments[endpoint.id]?.selected)
    .map((endpoint) => ({
      endpointId: endpoint.id,
      toolNames: assignments[endpoint.id]!.toolNames,
      timeoutSeconds: Number(assignments[endpoint.id]!.timeoutSeconds),
    }));
  if (selected.some((assignment) => assignment.toolNames.length === 0)) {
    error.value = t("remoteMcp.errors.assignmentToolsRequired");
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await managerApi.updateAgentRemoteMcpAssignments(selectedAgentId.value, selected);
    await queryClient.invalidateQueries({ queryKey: ["remote-mcp-assignments", selectedAgentId.value] });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("remoteMcp.errors.assignmentFailed");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="vt-page remote-mcp-page" data-page="mcp">
    <VtPageHeader :eyebrow="t('pages.mcp.eyebrow')" :title="t('pages.mcp.title')" :description="t('pages.mcp.description')">
      <template #actions>
        <VtButton v-if="canManage" data-remote-mcp-create @click="openCreate"><VtIcon name="plus" :size="16" /> {{ t("remoteMcp.create") }}</VtButton>
        <VtBadge v-else tone="neutral">{{ t("remoteMcp.access.readOnly") }}</VtBadge>
      </template>
    </VtPageHeader>

    <div v-if="!canManage" class="remote-mcp-access-note" role="status">
      <VtIcon name="warning" :size="18" />
      <p><b>{{ t(canTest ? "remoteMcp.access.operatorTitle" : "remoteMcp.access.viewerTitle") }}</b><span>{{ t(canTest ? "remoteMcp.access.operatorBody" : "remoteMcp.access.viewerBody") }}</span></p>
    </div>

    <div class="remote-mcp-summary">
      <article><span>{{ t("remoteMcp.metrics.endpoints") }}</span><b>{{ endpoints.length }}</b><small>{{ t("remoteMcp.metrics.immutable") }}</small></article>
      <article><span>{{ t("remoteMcp.metrics.healthy") }}</span><b>{{ healthyCount }}</b><small>{{ t("remoteMcp.metrics.healthHint") }}</small></article>
      <article><span>{{ t("remoteMcp.metrics.assistant") }}</span><b>{{ publishedAgents.length }}</b><small>{{ t("remoteMcp.metrics.publishHint") }}</small></article>
    </div>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <div v-if="queryError" class="remote-mcp-query-error" role="alert">
      <p>{{ queryError }}</p>
      <VtButton size="sm" variant="quiet" :busy="retryBusy" @click="retryQueries"><VtIcon name="refresh" :size="15" /> {{ t("shell.retry") }}</VtButton>
    </div>

    <VtEmptyState v-if="endpointsLoading" role="status" aria-live="polite" icon="tool" :title="t('shell.loadingTitle')" :text="t('shell.loadingBody')" />
    <div v-else-if="endpoints.length" class="remote-mcp-layout">
      <aside class="remote-mcp-list" :aria-label="t('remoteMcp.endpointListLabel')">
        <button v-for="endpoint in endpoints" :key="endpoint.id" type="button" :aria-pressed="selectedEndpoint?.id === endpoint.id" :class="{ active: selectedEndpoint?.id === endpoint.id }" @click="selectedEndpointId = endpoint.id">
          <span><VtIcon name="tool" :size="18" /></span>
          <div><b>{{ endpoint.name }}</b><small>{{ endpoint.transport }} · {{ t("remoteMcp.toolCount", { count: endpoint.tools.length }) }}</small><span class="sr-only">{{ healthLabel(endpoint) }}</span></div>
          <i :class="endpoint.health" aria-hidden="true"></i>
        </button>
      </aside>

      <article v-if="selectedEndpoint" class="vt-panel remote-mcp-detail">
        <header>
          <div><span class="vt-kicker">{{ t("remoteMcp.detailKicker") }}</span><h2>{{ selectedEndpoint.name }}</h2><p>{{ selectedEndpoint.url }}</p></div>
          <div><VtBadge :tone="endpointTone(selectedEndpoint)">{{ healthLabel(selectedEndpoint) }}</VtBadge><VtBadge tone="info">{{ selectedEndpoint.networkPolicy }}</VtBadge></div>
        </header>
        <dl class="remote-mcp-facts">
          <div><dt>{{ t("remoteMcp.transport") }}</dt><dd>{{ selectedEndpoint.transport }}</dd></div>
          <div><dt>{{ t("remoteMcp.auth") }}</dt><dd>{{ selectedEndpoint.authType }} · {{ selectedEndpoint.secretConfigured ? t("remoteMcp.secretStored") : t("remoteMcp.noSecret") }}</dd></div>
          <div><dt>{{ t("remoteMcp.timeout") }}</dt><dd>{{ t("remoteMcp.timeoutValue", { seconds: formatNumber(selectedEndpoint.timeoutSeconds) }) }}</dd></div>
          <div><dt>{{ t("remoteMcp.resultLimit") }}</dt><dd>{{ t("remoteMcp.resultLimitValue", { kib: formatNumber(Math.round(selectedEndpoint.resultMaxBytes / 1024)) }) }}</dd></div>
        </dl>
        <section class="remote-tool-policy">
          <div v-for="tool in selectedEndpoint.tools" :key="tool.name"><span><b>{{ tool.name }}</b><small>{{ tool.safetyClass }}</small></span><VtBadge v-if="tool.requiresConfirmation" tone="warning">{{ t("remoteMcp.confirmation") }}</VtBadge></div>
        </section>
        <footer>
          <small v-if="selectedEndpoint.healthCheckedAt">{{ t("remoteMcp.checkedAt", { date: formatDate(selectedEndpoint.healthCheckedAt) }) }}</small>
          <small v-else>{{ t("remoteMcp.notChecked") }}</small>
          <div><VtButton v-if="canTest" data-remote-mcp-test size="sm" variant="quiet" :busy="testBusy === selectedEndpoint.id" @click="testEndpoint(selectedEndpoint)"><VtIcon name="play" :size="15" /> {{ t("remoteMcp.test") }}</VtButton><VtButton v-if="canManage && selectedEndpoint.authType !== 'none'" data-remote-mcp-secret size="sm" variant="quiet" @click="openSecret(selectedEndpoint)"><VtIcon name="refresh" :size="15" /> {{ t("remoteMcp.rotateSecret") }}</VtButton><VtButton v-if="canManage" data-remote-mcp-toggle size="sm" :variant="selectedEndpoint.enabled ? 'danger' : 'quiet'" :busy="busy" @click="toggleEndpoint(selectedEndpoint)">{{ t(selectedEndpoint.enabled ? "remoteMcp.disable" : "remoteMcp.enable") }}</VtButton></div>
        </footer>
      </article>
    </div>
    <VtEmptyState v-else-if="!queryError" icon="tool" :title="t('remoteMcp.emptyTitle')" :text="t('remoteMcp.emptyBody')"><VtButton @click="openCreate"><VtIcon name="plus" :size="16" /> {{ t("remoteMcp.create") }}</VtButton></VtEmptyState>

    <article class="vt-panel remote-mcp-assignment">
      <header class="panel-header"><div><span class="vt-kicker">{{ t("remoteMcp.assignment.kicker") }}</span><h2>{{ t("remoteMcp.assignment.title") }}</h2><p>{{ t("remoteMcp.assignment.description") }}</p></div><VtField :label="t('remoteMcp.assignment.agent')"><VtSelect v-model="selectedAgentId"><option value="">{{ t("remoteMcp.assignment.noAgent") }}</option><option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option></VtSelect></VtField></header>
      <VtEmptyState v-if="assignmentsLoading" role="status" aria-live="polite" icon="tool" :title="t('shell.loadingTitle')" :text="t('shell.loadingBody')" />
      <div v-else-if="selectedAgentId && endpoints.length && !assignmentsQuery.error.value" class="assignment-list">
        <section v-for="endpoint in endpoints" :key="endpoint.id" :class="{ selected: assignments[endpoint.id]?.selected }">
          <header><label class="switch-control"><input v-model="assignments[endpoint.id]!.selected" type="checkbox" :disabled="!canManage" /><span></span><b>{{ endpoint.name }}</b></label><VtBadge :tone="endpointTone(endpoint)">{{ healthLabel(endpoint) }}</VtBadge></header>
          <div v-if="assignments[endpoint.id]?.selected" class="assignment-tools">
            <label v-for="tool in endpoint.tools" :key="tool.name"><input type="checkbox" :disabled="!canManage" :checked="assignments[endpoint.id]!.toolNames.includes(tool.name)" @change="setTool(endpoint.id, tool.name, ($event.target as HTMLInputElement).checked)" /><span><b>{{ tool.name }}</b><small>{{ tool.safetyClass }}</small></span></label>
            <VtField :label="t('remoteMcp.assignment.timeout')"><VtInput v-model="assignments[endpoint.id]!.timeoutSeconds" data-assignment-timeout type="number" min="5" max="30" step="1" :disabled="!canManage" /></VtField>
          </div>
        </section>
        <div class="assignment-footer"><p>{{ t("remoteMcp.assignment.publishNote") }}</p><VtButton v-if="canManage" data-remote-mcp-assignment-save :busy="busy" @click="saveAssignments"><VtIcon name="check" :size="16" /> {{ t("remoteMcp.assignment.save") }}</VtButton><VtBadge v-else tone="neutral">{{ t("remoteMcp.access.readOnly") }}</VtBadge></div>
      </div>
      <VtEmptyState v-else-if="!queryError" icon="tool" :title="t('remoteMcp.assignment.emptyTitle')" :text="t('remoteMcp.assignment.emptyBody')" />
    </article>

    <VtDialog :open="createOpen" :title="t('remoteMcp.dialog.title')" :eyebrow="t('remoteMcp.dialog.eyebrow')" icon="tool" :description="t('remoteMcp.dialog.description')" width="lg" @close="closeCreate">
      <form id="remote-mcp-create-form" class="form-stack" @submit.prevent="createEndpoint">
        <div class="form-grid two"><VtField :label="t('remoteMcp.form.name')" required><VtInput v-model="form.name" maxlength="80" required /></VtField><VtField :label="t('remoteMcp.form.url')" required><VtInput v-model="form.url" data-remote-mcp-url type="url" placeholder="https://mcp.example.com/mcp" required /></VtField><VtField :label="t('remoteMcp.form.transport')" :hint="t('remoteMcp.form.transportHint')"><VtSelect v-model="form.transport"><option value="streamable_http">{{ t("remoteMcp.form.streamableHttp") }}</option><option value="sse" disabled>{{ t("remoteMcp.form.sseLegacy") }}</option></VtSelect></VtField><VtField :label="t('remoteMcp.form.networkPolicy')"><VtSelect v-model="form.networkPolicy"><option value="public_only">public_only</option><option value="private_allowlist">private_allowlist</option></VtSelect></VtField><VtField class="span-two" :label="t('remoteMcp.form.allowedHosts')" :hint="t('remoteMcp.form.allowedHostsHint')"><VtInput :model-value="endpointHostname" readonly /></VtField><VtField :label="t('remoteMcp.form.authType')"><VtSelect v-model="form.authType"><option value="none">none</option><option value="bearer">bearer</option><option value="header">header</option></VtSelect></VtField><VtField v-if="form.authType === 'header'" :label="t('remoteMcp.form.headerName')" required><VtInput v-model="form.authHeaderName" maxlength="80" required /></VtField><VtField v-if="form.authType !== 'none'" :label="t('remoteMcp.form.secret')" :hint="t('remoteMcp.form.secretHint')" required><VtInput v-model="form.secret" data-remote-mcp-create-secret type="password" autocomplete="new-password" required /></VtField><VtField :label="t('remoteMcp.form.timeout')"><VtInput v-model="form.timeoutSeconds" type="number" min="5" max="30" step="1" /></VtField><VtField :label="t('remoteMcp.form.resultLimit')"><VtInput v-model="form.resultMaxBytes" type="number" min="1024" max="65536" step="1024" /></VtField></div>
        <section class="remote-tool-editor"><header><div><b>{{ t("remoteMcp.form.tools") }}</b><small>{{ t("remoteMcp.form.toolsHint") }}</small></div><VtButton type="button" variant="quiet" size="sm" @click="form.tools.push(newToolDraft())"><VtIcon name="plus" :size="14" /> {{ t("remoteMcp.form.addTool") }}</VtButton></header><div v-for="(tool, index) in form.tools" :key="tool.id" class="remote-tool-row"><VtField :label="t('remoteMcp.form.toolName')"><VtInput v-model="tool.name" placeholder="weather.current" /></VtField><VtField :label="t('remoteMcp.form.safety')"><VtSelect v-model="tool.safetyClass"><option value="read_only">read_only</option><option value="reversible">reversible</option><option value="disruptive">disruptive</option><option value="destructive">destructive</option></VtSelect></VtField><label class="switch-control"><input v-model="tool.requiresConfirmation" type="checkbox" /><span></span><b>{{ t("remoteMcp.confirmation") }}</b></label><button type="button" class="vt-icon-button" :aria-label="t('remoteMcp.form.removeTool')" :disabled="form.tools.length === 1" @click="form.tools.splice(index, 1)"><VtIcon name="trash" :size="16" /></button></div></section>
        <div class="remote-mcp-security-note"><VtIcon name="warning" :size="18" /><p><b>{{ t("remoteMcp.form.securityTitle") }}</b><span>{{ t("remoteMcp.form.securityBody") }}</span></p></div>
        <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      </form>
      <template #footer><VtButton class="remote-mcp-dialog-action" variant="quiet" @click="closeCreate">{{ t("common.cancel") }}</VtButton><VtButton class="remote-mcp-dialog-action" form="remote-mcp-create-form" type="submit" :busy="busy"><VtIcon name="plus" :size="16" /> {{ t("remoteMcp.dialog.submit") }}</VtButton></template>
    </VtDialog>

    <VtDialog :open="secretOpen" :title="t('remoteMcp.secretDialog.title')" :eyebrow="t('remoteMcp.secretDialog.eyebrow')" icon="warning" :description="t('remoteMcp.secretDialog.description')" width="sm" @close="closeSecret">
      <form id="remote-mcp-secret-form" class="form-stack" @submit.prevent="updateSecret"><VtField :label="t('remoteMcp.secretDialog.action')"><VtSelect v-model="secretAction"><option value="rotate">{{ t("remoteMcp.secretDialog.rotate") }}</option><option value="clear">{{ t("remoteMcp.secretDialog.clear") }}</option></VtSelect></VtField><VtField v-if="secretAction === 'rotate'" :label="t('remoteMcp.form.secret')" required><VtInput v-model="secretValue" data-remote-mcp-rotate-secret type="password" autocomplete="new-password" required /></VtField><p v-if="error" class="inline-error" role="alert">{{ error }}</p></form>
      <template #footer><VtButton class="remote-mcp-dialog-action" variant="quiet" @click="closeSecret">{{ t("common.cancel") }}</VtButton><VtButton class="remote-mcp-dialog-action" form="remote-mcp-secret-form" type="submit" :variant="secretAction === 'clear' ? 'danger' : 'primary'" :busy="busy">{{ t("remoteMcp.secretDialog.submit") }}</VtButton></template>
    </VtDialog>
  </section>
</template>

<style scoped>
.remote-mcp-page { display: grid; gap: 18px; }
.remote-mcp-page > :deep(.vt-page-header) { margin-bottom: 0; }
.remote-mcp-access-note,
.remote-mcp-query-error { display: flex; align-items: center; gap: 12px; border: 1px solid var(--line); border-radius: 14px; padding: 13px 15px; background: var(--paper-strong); }
.remote-mcp-access-note { color: var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--paper-strong)); }
.remote-mcp-access-note p { display: grid; gap: 3px; margin: 0; }
.remote-mcp-access-note b { color: var(--ink); font-size: 14px; }
.remote-mcp-access-note span { color: var(--muted); font-size: 12px; line-height: 1.5; }
.remote-mcp-query-error { justify-content: space-between; color: var(--danger); }
.remote-mcp-query-error p { margin: 0; font-size: 14px; }
.remote-mcp-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.remote-mcp-summary article { display: grid; gap: 4px; border: 1px solid var(--line); border-radius: 15px; padding: 15px 17px; background: var(--paper-strong); }
.remote-mcp-summary span,
.remote-mcp-summary small { color: var(--muted); font-size: 12px; }
.remote-mcp-summary b { font-size: 28px; }
.remote-mcp-layout { display: grid; grid-template-columns: 300px minmax(0,1fr); gap: 16px; }
.remote-mcp-list { display: grid; align-content: start; gap: 7px; }
.remote-mcp-list button { display: grid; grid-template-columns: 38px minmax(0,1fr) 8px; align-items: center; gap: 10px; border: 1px solid transparent; border-radius: 14px; padding: 11px; background: transparent; text-align: left; }
.remote-mcp-list button.active { border-color: var(--line); background: var(--paper-strong); box-shadow: var(--shadow-sm); }
.remote-mcp-list button > span { display: grid; width: 36px; height: 36px; place-items: center; border-radius: 11px; color: var(--navy-2); background: var(--blue); }
.remote-mcp-list button div { display: grid; min-width: 0; gap: 3px; }
.remote-mcp-list b { overflow: hidden; font-size: 14px; text-overflow: ellipsis; }
.remote-mcp-list small { color: var(--muted); font-size: 12px; }
.remote-mcp-list i { width: 8px; height: 8px; border-radius: 50%; background: #aeb8b3; }
.remote-mcp-list i.healthy { background: var(--success); }
.remote-mcp-list i.degraded { background: var(--danger); }
.remote-mcp-detail { overflow: hidden; padding: 0; }
.remote-mcp-detail > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; border-bottom: 1px solid var(--line); padding: 22px; }
.remote-mcp-detail h2 { margin: 5px 0; font-size: 23px; }
.remote-mcp-detail header p { overflow-wrap: anywhere; margin: 0; color: var(--muted); font-size: 12px; }
.remote-mcp-detail header > div:last-child { display: flex; flex-wrap: wrap; gap: 6px; }
.remote-mcp-facts { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 1px; margin: 0; background: var(--line); }
.remote-mcp-facts div { display: grid; gap: 4px; padding: 12px; background: var(--paper-strong); }
.remote-mcp-facts dt { color: var(--muted); font-size: 12px; }
.remote-mcp-facts dd { overflow-wrap: anywhere; margin: 0; font-size: 14px; font-weight: 700; }
.remote-tool-policy { display: grid; padding: 18px 22px; }
.remote-tool-policy > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid var(--line); padding: 10px 0; }
.remote-tool-policy > div:first-child { border-top: 0; }
.remote-tool-policy span { display: grid; gap: 3px; }
.remote-tool-policy b { overflow-wrap: anywhere; font-size: 14px; }
.remote-tool-policy small { color: var(--muted); font-size: 12px; }
.remote-mcp-detail > footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-top: 1px solid var(--line); padding: 14px 22px; }
.remote-mcp-detail footer small { color: var(--muted); font-size: 12px; }
.remote-mcp-detail footer div { display: flex; flex-wrap: wrap; gap: 6px; }
.remote-mcp-assignment { padding: 22px; }
.remote-mcp-assignment > .panel-header { grid-template-columns: minmax(0,1fr) minmax(240px,320px); }
.assignment-list { display: grid; gap: 9px; margin-top: 17px; }
.assignment-list > section { border: 1px solid var(--line); border-radius: 15px; padding: 12px 14px; background: var(--paper); }
.assignment-list > section.selected { border-color: color-mix(in srgb,var(--success) 40%,var(--line)); }
.assignment-list section > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.assignment-tools { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 8px; border-top: 1px solid var(--line); margin-top: 10px; padding-top: 10px; }
.assignment-tools > label { display: flex; min-height: 44px; align-items: center; gap: 9px; border: 1px solid var(--line); border-radius: 11px; padding: 8px 10px; background: var(--paper-strong); }
.assignment-tools > label input[type="checkbox"] { width: 18px; height: 18px; flex: none; accent-color: var(--success); }
.assignment-tools span { display: grid; gap: 2px; }
.assignment-tools b { overflow-wrap: anywhere; font-size: 14px; }
.assignment-tools small { color: var(--muted); font-size: 12px; }
.assignment-tools > :deep(.vt-field) { margin: 0; }
.assignment-tools :deep(.vt-input) { width: 100%; min-width: 0; font-size: 14px; }
.assignment-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; border-top: 1px solid var(--line); padding-top: 14px; }
.assignment-footer p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
.remote-tool-editor { display: grid; gap: 9px; border: 1px solid var(--line); border-radius: 14px; padding: 13px; }
.form-stack :deep(.vt-field),
.form-stack :deep(.vt-control),
.form-stack :deep(.vt-button),
.remote-tool-row .switch-control b,
.remote-mcp-dialog-action { font-size: 14px; }
.form-stack :deep(.vt-field-hint),
.form-stack :deep(.vt-field-error) { font-size: 12px; }
.remote-tool-editor > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.remote-tool-editor header div { display: grid; gap: 3px; }
.remote-tool-editor header b { font-size: 14px; }
.remote-tool-editor header small { color: var(--muted); font-size: 12px; }
.remote-tool-row { display: grid; grid-template-columns: minmax(180px,1fr) minmax(150px,.7fr) minmax(160px,.7fr) 44px; align-items: end; gap: 8px; }
.remote-tool-row > :deep(.vt-field) { margin: 0; font-size: 14px; }
.remote-tool-row .switch-control { min-height: 44px; }
.remote-tool-row .vt-icon-button { width: 44px; height: 44px; margin-bottom: 1px; }
.remote-mcp-security-note { display: grid; grid-template-columns: 32px minmax(0,1fr); gap: 10px; border: 1px solid color-mix(in srgb, var(--warning) 35%, var(--line)); border-radius: 13px; padding: 12px; color: var(--warning); background: color-mix(in srgb, var(--warning) 8%, var(--paper-strong)); }
.remote-mcp-security-note p { display: grid; gap: 3px; margin: 0; }
.remote-mcp-security-note b { color: var(--ink); font-size: 14px; }
.remote-mcp-security-note span { color: var(--muted); font-size: 12px; line-height: 1.5; }
@media (max-width: 1100px) { .remote-mcp-layout { grid-template-columns: 240px minmax(0,1fr); }.remote-mcp-facts { grid-template-columns: repeat(2,1fr); }.assignment-tools { grid-template-columns: repeat(2,1fr); }.remote-tool-row { grid-template-columns: 1fr 1fr; }.remote-tool-row .vt-icon-button { justify-self: end; } }
@media (max-width: 760px) { .remote-mcp-summary { grid-template-columns: 1fr; }.remote-mcp-layout { grid-template-columns: 1fr; }.remote-mcp-list { display: flex; overflow-x: auto; }.remote-mcp-list button { min-width: 250px; }.remote-mcp-detail > header,.remote-mcp-detail > footer,.assignment-footer,.remote-mcp-query-error { display: grid; }.remote-mcp-detail footer div { justify-content: flex-start; }.remote-mcp-assignment > .panel-header { grid-template-columns: 1fr; }.assignment-tools,.remote-tool-row { grid-template-columns: 1fr; }.remote-tool-row .vt-icon-button { justify-self: start; }.remote-mcp-facts { grid-template-columns: 1fr; } }
</style>
