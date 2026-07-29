<script setup lang="ts">
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, nextTick, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { managerApi } from "../../api/client";
import type { Agent, Device, MemoryFact, MemoryMessage } from "../../api/schemas";
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
  VtTextarea,
} from "../ui";

const props = defineProps<{ agents: Agent[]; devices: Device[] }>();
const { locale, t } = useI18n();
const queryClient = useQueryClient();
const selectedAgentId = ref("");
const selectedDeviceId = ref("");
const tab = ref<"messages" | "facts">("messages");
const messageRows = ref<MemoryMessage[]>([]);
const factRows = ref<MemoryFact[]>([]);
const messageCursor = ref<string>();
const factCursor = ref<string>();
const loadingMore = ref(false);
const retryBusy = ref(false);
const exporting = ref(false);
const busy = ref(false);
const error = ref("");
const loadMoreError = ref("");
const messagesTabButton = ref<HTMLButtonElement>();
const factsTabButton = ref<HTMLButtonElement>();
const editOpen = ref(false);
const editFact = ref<MemoryFact>();
const editValue = ref("");
const editConfidence = ref(0.8);
const editExpiresAt = ref("");
const deleteOpen = ref(false);
const deleteTarget = ref<{ kind: "message" | "fact" | "purge"; id?: string; label: string }>();

const selectedAgent = computed(() => props.agents.find((agent) => agent.id === selectedAgentId.value));
const selectedDevice = computed(() => props.devices.find((device) => device.id === selectedDeviceId.value));
const agentDevices = computed(() => props.devices.filter((device) => device.agentId === selectedAgentId.value));
const policy = computed(() => selectedAgent.value?.publishedMemoryPolicy);
const policyEnabled = computed(() => policy.value?.enabled === true && policy.value.consent === true);

watch(
  () => props.agents,
  (agents) => {
    if (!agents.some((agent) => agent.id === selectedAgentId.value)) selectedAgentId.value = agents[0]?.id ?? "";
  },
  { immediate: true },
);

watch(agentDevices, (devices) => {
  if (!devices.some((device) => device.id === selectedDeviceId.value)) {
    selectedDeviceId.value = devices[0]?.id ?? "";
  }
}, { immediate: true });

watch([selectedAgentId, selectedDeviceId], () => {
  messageRows.value = [];
  factRows.value = [];
  messageCursor.value = undefined;
  factCursor.value = undefined;
  error.value = "";
  loadMoreError.value = "";
});

watch(tab, () => {
  loadMoreError.value = "";
});

const messagesQuery = useQuery({
  queryKey: computed(() => ["memory-messages", selectedAgentId.value, selectedDeviceId.value]),
  queryFn: () => managerApi.memoryMessages(selectedAgentId.value, {
    deviceId: selectedDeviceId.value,
    limit: 100,
  }),
  enabled: computed(() => Boolean(selectedAgentId.value && selectedDeviceId.value)),
  retry: 1,
});
const factsQuery = useQuery({
  queryKey: computed(() => ["memory-facts", selectedAgentId.value, selectedDeviceId.value]),
  queryFn: () => managerApi.memoryFacts(selectedAgentId.value, {
    deviceId: selectedDeviceId.value,
    limit: 100,
  }),
  enabled: computed(() => Boolean(selectedAgentId.value && selectedDeviceId.value)),
  retry: 1,
});
const memoryLoading = computed(() => (
  Boolean(selectedDeviceId.value) && (messagesQuery.isPending.value || factsQuery.isPending.value)
));
const memoryQueryError = computed(() => {
  const value = messagesQuery.error.value ?? factsQuery.error.value;
  return value instanceof Error ? value.message : value ? t("memory.errors.loadFailed") : "";
});

watch(
  () => messagesQuery.data.value,
  (page) => {
    messageRows.value = page?.items ?? [];
    messageCursor.value = page?.nextCursor;
  },
  { immediate: true },
);
watch(
  () => factsQuery.data.value,
  (page) => {
    factRows.value = page?.items ?? [];
    factCursor.value = page?.nextCursor;
  },
  { immediate: true },
);

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value);
}

function formatPercent(value: number): string {
  return new Intl.NumberFormat(locale.value, { style: "percent", maximumFractionDigits: 0 }).format(value);
}

async function setTab(value: "messages" | "facts", focus = false): Promise<void> {
  tab.value = value;
  if (!focus) return;
  await nextTick();
  (value === "messages" ? messagesTabButton.value : factsTabButton.value)?.focus();
}

function handleTabKeydown(event: KeyboardEvent): void {
  let next: "messages" | "facts" | undefined;
  if (event.key === "ArrowLeft" || event.key === "ArrowUp" || event.key === "Home") next = "messages";
  if (event.key === "ArrowRight" || event.key === "ArrowDown" || event.key === "End") next = "facts";
  if (!next) return;
  event.preventDefault();
  void setTab(next, true);
}

async function retryMemoryQueries(): Promise<void> {
  retryBusy.value = true;
  error.value = "";
  try {
    await Promise.all([messagesQuery.refetch(), factsQuery.refetch()]);
  } finally {
    retryBusy.value = false;
  }
}

async function loadMoreMessages(): Promise<void> {
  if (!messageCursor.value || !selectedAgentId.value || !selectedDeviceId.value) return;
  loadingMore.value = true;
  loadMoreError.value = "";
  try {
    const page = await managerApi.memoryMessages(selectedAgentId.value, {
      deviceId: selectedDeviceId.value,
      limit: 100,
      cursor: messageCursor.value,
    });
    const seen = new Set(messageRows.value.map((message) => message.id));
    messageRows.value = [
      ...messageRows.value,
      ...page.items.filter((message) => !seen.has(message.id)),
    ];
    messageCursor.value = page.nextCursor;
  } catch (exception) {
    loadMoreError.value = exception instanceof Error ? exception.message : t("memory.errors.paginationFailed");
  } finally {
    loadingMore.value = false;
  }
}

async function loadMoreFacts(): Promise<void> {
  if (!factCursor.value || !selectedAgentId.value || !selectedDeviceId.value) return;
  loadingMore.value = true;
  loadMoreError.value = "";
  try {
    const page = await managerApi.memoryFacts(selectedAgentId.value, {
      deviceId: selectedDeviceId.value,
      limit: 100,
      cursor: factCursor.value,
    });
    const seen = new Set(factRows.value.map((fact) => fact.id));
    factRows.value = [
      ...factRows.value,
      ...page.items.filter((fact) => !seen.has(fact.id)),
    ];
    factCursor.value = page.nextCursor;
  } catch (exception) {
    loadMoreError.value = exception instanceof Error ? exception.message : t("memory.errors.paginationFailed");
  } finally {
    loadingMore.value = false;
  }
}

function openFactEdit(fact: MemoryFact): void {
  editFact.value = fact;
  editValue.value = fact.value;
  editConfidence.value = fact.confidence;
  const date = new Date(fact.expiresAt);
  editExpiresAt.value = Number.isNaN(date.getTime())
    ? ""
    : new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
  error.value = "";
  editOpen.value = true;
}

async function saveFact(): Promise<void> {
  const fact = editFact.value;
  if (!fact || !selectedAgentId.value || !editValue.value.trim()) return;
  busy.value = true;
  error.value = "";
  try {
    await managerApi.updateMemoryFact(selectedAgentId.value, fact.id, {
      value: editValue.value.trim(),
      confidence: Number(editConfidence.value),
      ...(editExpiresAt.value ? { expiresAt: new Date(editExpiresAt.value).toISOString() } : {}),
    });
    await refreshMemory();
    editOpen.value = false;
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("memory.errors.updateFailed");
  } finally {
    busy.value = false;
  }
}

function askDelete(kind: "message" | "fact" | "purge", id: string | undefined, label: string): void {
  deleteTarget.value = { kind, ...(id ? { id } : {}), label };
  error.value = "";
  deleteOpen.value = true;
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value;
  if (!target || !selectedAgentId.value || !selectedDeviceId.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (target.kind === "message" && target.id) {
      await managerApi.deleteMemoryMessage(selectedAgentId.value, target.id);
    } else if (target.kind === "fact" && target.id) {
      await managerApi.deleteMemoryFact(selectedAgentId.value, target.id);
    } else if (target.kind === "purge") {
      await managerApi.purgeMemoryMessages(selectedAgentId.value, selectedDeviceId.value);
    }
    await refreshMemory();
    deleteOpen.value = false;
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("memory.errors.deleteFailed");
  } finally {
    busy.value = false;
  }
}

async function refreshMemory(): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["memory-messages", selectedAgentId.value, selectedDeviceId.value] }),
    queryClient.invalidateQueries({ queryKey: ["memory-facts", selectedAgentId.value, selectedDeviceId.value] }),
  ]);
}

async function exportVisible(): Promise<void> {
  if (!selectedAgentId.value || !selectedDeviceId.value) return;
  exporting.value = true;
  error.value = "";
  try {
    const payload = await managerApi.exportMemory(selectedAgentId.value, selectedDeviceId.value);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `veetee-memory-${payload.agentId}-${payload.exportedAt.slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("memory.errors.exportFailed");
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <section class="vt-page memory-page" data-page="memory">
    <VtPageHeader :eyebrow="t('pages.memory.eyebrow')" :title="t('pages.memory.title')" :description="t('pages.memory.description')">
      <template #actions><VtButton variant="quiet" :busy="exporting" :disabled="!selectedAgentId || !selectedDeviceId || Boolean(memoryQueryError)" @click="exportVisible"><VtIcon name="upload" :size="16" /> {{ t("memory.export") }}</VtButton></template>
    </VtPageHeader>

    <div class="memory-toolbar">
      <VtField :label="t('memory.agent')"><VtSelect v-model="selectedAgentId"><option value="">{{ t("memory.noAgent") }}</option><option v-for="agent in agents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option></VtSelect></VtField>
      <VtField :label="t('memory.device')"><VtSelect v-model="selectedDeviceId"><option value="" disabled>{{ t("memory.selectDevice") }}</option><option v-for="device in agentDevices" :key="device.id" :value="device.id">{{ device.name }}</option></VtSelect></VtField>
      <div class="memory-policy-state"><VtBadge :tone="policyEnabled ? 'success' : 'neutral'" dot>{{ t(policyEnabled ? "memory.policyEnabled" : "memory.policyDisabled") }}</VtBadge><p>{{ t(policyEnabled ? "memory.policyEnabledHint" : "memory.policyDisabledHint") }}</p></div>
    </div>

    <div v-if="selectedAgent && selectedDevice" class="memory-summary">
      <article><span>{{ t("memory.metrics.messages") }}</span><b>{{ messageRows.length }}</b><small>{{ t("memory.metrics.loaded") }}</small></article>
      <article><span>{{ t("memory.metrics.facts") }}</span><b>{{ factRows.length }}</b><small>{{ t("memory.metrics.structured") }}</small></article>
      <article><span>{{ t("memory.metrics.scope") }}</span><b>{{ selectedDevice.name }}</b><small>{{ t("memory.metrics.scopeHint") }}</small></article>
      <article><span>{{ t("memory.metrics.retention") }}</span><b>{{ t("memory.metrics.retentionValue", { days: Number(policy?.retentionDays ?? 7) }) }}</b><small>{{ t("memory.metrics.expiry") }}</small></article>
    </div>

    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <div v-if="memoryQueryError" class="memory-query-error" role="alert">
      <p>{{ memoryQueryError }}</p>
      <VtButton size="sm" variant="quiet" :busy="retryBusy" @click="retryMemoryQueries"><VtIcon name="refresh" :size="15" /> {{ t("shell.retry") }}</VtButton>
    </div>

    <article v-if="selectedAgent && selectedDevice" class="vt-panel memory-workspace">
      <header class="memory-tabs">
        <div role="tablist" :aria-label="t('memory.tabs.label')"><button id="memory-tab-messages" ref="messagesTabButton" type="button" role="tab" aria-controls="memory-panel-messages" :aria-selected="tab === 'messages'" :tabindex="tab === 'messages' ? 0 : -1" :class="{ active: tab === 'messages' }" @click="setTab('messages')" @keydown="handleTabKeydown">{{ t("memory.tabs.messages") }}</button><button id="memory-tab-facts" ref="factsTabButton" type="button" role="tab" aria-controls="memory-panel-facts" :aria-selected="tab === 'facts'" :tabindex="tab === 'facts' ? 0 : -1" :class="{ active: tab === 'facts' }" @click="setTab('facts')" @keydown="handleTabKeydown">{{ t("memory.tabs.facts") }}</button></div>
        <VtButton v-if="tab === 'messages' && messageRows.length" variant="danger" size="sm" @click="askDelete('purge', undefined, selectedDevice.name)"><VtIcon name="trash" :size="15" /> {{ t("memory.purge") }}</VtButton>
      </header>

      <section v-if="tab === 'messages'" id="memory-panel-messages" class="memory-message-list" role="tabpanel" aria-labelledby="memory-tab-messages">
        <article v-for="message in messageRows" :key="message.id" :class="message.role">
          <header><span><VtBadge :tone="message.role === 'user' ? 'info' : 'success'">{{ t(`memory.roles.${message.role}`) }}</VtBadge><time>{{ formatDate(message.occurredAt) }}</time></span><button type="button" class="vt-icon-button" :aria-label="t('memory.deleteMessage')" @click="askDelete('message', message.id, message.content.slice(0, 80))"><VtIcon name="trash" :size="15" /></button></header>
          <p>{{ message.content }}</p>
          <footer><small>{{ message.sessionId }} · {{ message.turnId }}</small><small>{{ t("memory.expires", { date: formatDate(message.retentionUntil) }) }}</small></footer>
        </article>
        <p v-if="loadMoreError" class="inline-error" role="alert">{{ loadMoreError }}</p>
        <VtButton v-if="messageCursor" variant="quiet" :busy="loadingMore" @click="loadMoreMessages">{{ t(loadMoreError ? "memory.retryLoadMore" : "memory.loadMore") }}</VtButton>
        <VtEmptyState v-if="memoryLoading && !messageRows.length" role="status" aria-live="polite" icon="memory" :title="t('shell.loadingTitle')" :text="t('shell.loadingBody')" />
        <VtEmptyState v-else-if="!memoryQueryError && !messageRows.length" icon="memory" :title="t('memory.messagesEmptyTitle')" :text="t('memory.messagesEmptyBody')" />
      </section>

      <section v-else id="memory-panel-facts" class="memory-fact-list" role="tabpanel" aria-labelledby="memory-tab-facts">
        <article v-for="fact in factRows" :key="fact.id">
          <header><div><span class="vt-kicker">{{ fact.category }}</span><h3>{{ fact.key }}</h3></div><VtBadge :tone="fact.confidence >= .8 ? 'success' : 'warning'">{{ formatPercent(fact.confidence) }}</VtBadge></header>
          <p>{{ fact.value }}</p>
          <dl><div><dt>{{ t("memory.fact.source") }}</dt><dd>{{ fact.sourceSessionId }} · {{ fact.sourceTurnId }}</dd></div><div><dt>{{ t("memory.fact.expires") }}</dt><dd>{{ formatDate(fact.expiresAt) }}</dd></div></dl>
          <footer><VtButton variant="quiet" size="sm" @click="openFactEdit(fact)"><VtIcon name="edit" :size="14" /> {{ t("memory.edit") }}</VtButton><VtButton variant="danger" size="sm" @click="askDelete('fact', fact.id, `${fact.key}: ${fact.value}`)"><VtIcon name="trash" :size="14" /> {{ t("memory.delete") }}</VtButton></footer>
        </article>
        <p v-if="loadMoreError" class="inline-error" role="alert">{{ loadMoreError }}</p>
        <VtButton v-if="factCursor" variant="quiet" :busy="loadingMore" @click="loadMoreFacts">{{ t(loadMoreError ? "memory.retryLoadMore" : "memory.loadMore") }}</VtButton>
        <VtEmptyState v-if="memoryLoading && !factRows.length" role="status" aria-live="polite" icon="memory" :title="t('shell.loadingTitle')" :text="t('shell.loadingBody')" />
        <VtEmptyState v-else-if="!memoryQueryError && !factRows.length" icon="memory" :title="t('memory.factsEmptyTitle')" :text="t('memory.factsEmptyBody')" />
      </section>
    </article>
    <VtEmptyState v-else-if="selectedAgent" icon="memory" :title="t('memory.noDeviceTitle')" :text="t('memory.noDeviceBody')" />
    <VtEmptyState v-else icon="memory" :title="t('memory.noAgentTitle')" :text="t('memory.noAgentBody')" />

    <VtDialog :open="editOpen" :title="t('memory.editDialog.title')" :eyebrow="t('memory.editDialog.eyebrow')" icon="memory" :description="t('memory.editDialog.description')" width="sm" @close="editOpen = false">
      <form id="memory-fact-edit-form" class="form-stack" @submit.prevent="saveFact"><VtField :label="t('memory.editDialog.value')" required><VtTextarea v-model="editValue" rows="5" maxlength="2000" required /></VtField><div class="form-grid two"><VtField :label="t('memory.editDialog.confidence')"><VtInput v-model="editConfidence" type="number" min="0" max="1" step="0.01" /></VtField><VtField :label="t('memory.editDialog.expiresAt')"><VtInput v-model="editExpiresAt" type="datetime-local" /></VtField></div><p v-if="error" class="inline-error" role="alert">{{ error }}</p></form>
      <template #footer><VtButton variant="quiet" @click="editOpen = false">{{ t("common.cancel") }}</VtButton><VtButton form="memory-fact-edit-form" type="submit" :busy="busy">{{ t("memory.editDialog.submit") }}</VtButton></template>
    </VtDialog>

    <VtDialog :open="deleteOpen" :title="t(deleteTarget?.kind === 'purge' ? 'memory.deleteDialog.purgeTitle' : 'memory.deleteDialog.title')" :eyebrow="t('memory.deleteDialog.eyebrow')" icon="warning" :description="t(deleteTarget?.kind === 'purge' ? 'memory.deleteDialog.purgeDescription' : 'memory.deleteDialog.description')" width="sm" @close="deleteOpen = false">
      <div class="memory-delete-preview"><VtIcon name="trash" :size="19" /><p>{{ deleteTarget?.label }}</p></div><p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <template #footer><VtButton variant="quiet" @click="deleteOpen = false">{{ t("common.cancel") }}</VtButton><VtButton variant="danger" :busy="busy" @click="confirmDelete">{{ t("memory.deleteDialog.submit") }}</VtButton></template>
    </VtDialog>
  </section>
</template>

<style scoped>
.memory-page { display: grid; gap: 18px; }
.memory-page > :deep(.vt-page-header) { margin-bottom: 0; }
.memory-query-error { display: flex; align-items: center; justify-content: space-between; gap: 12px; border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--line)); border-radius: 14px; padding: 13px 15px; color: var(--danger); background: color-mix(in srgb, var(--danger) 7%, var(--paper-strong)); }
.memory-query-error p { margin: 0; font-size: 14px; }
.memory-toolbar { display: grid; grid-template-columns: minmax(220px,1fr) minmax(220px,1fr) minmax(280px,1.2fr); align-items: end; gap: 12px; border: 1px solid var(--line); border-radius: 16px; padding: 14px; background: var(--paper-strong); }
.memory-toolbar :deep(.vt-field) { font-size: 14px; }
.memory-policy-state { display: grid; align-content: center; gap: 6px; min-height: 64px; border-left: 1px solid var(--line); padding-left: 16px; }
.memory-policy-state .vt-badge { justify-self: start; }
.memory-policy-state p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
.memory-summary { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 8px; }
.memory-summary article { display: grid; min-width: 0; gap: 4px; border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; background: var(--paper-strong); }
.memory-summary span,
.memory-summary small { color: var(--muted); font-size: 12px; }
.memory-summary b { overflow: hidden; font-size: 22px; text-overflow: ellipsis; white-space: nowrap; }
.memory-workspace { overflow: hidden; padding: 0; }
.memory-tabs { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 12px 16px; }
.memory-tabs > div { display: flex; gap: 4px; }
.memory-tabs [role=tab] { min-height: 44px; border: 0; border-radius: 10px; padding: 0 14px; color: var(--muted); background: transparent; font-size: 14px; font-weight: 700; }
.memory-tabs [role=tab].active { color: var(--navy); background: var(--blue); }
.memory-message-list { display: grid; gap: 9px; padding: 18px; }
.memory-message-list > article { display: grid; gap: 8px; border: 1px solid var(--line); border-radius: 15px; padding: 13px 15px; background: var(--paper); }
.memory-message-list > article.assistant { margin-left: 5%; }
.memory-message-list > article.user { margin-right: 5%; }
.memory-message-list article > header,
.memory-message-list article > footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.memory-message-list header span { display: flex; align-items: center; gap: 9px; }
.memory-message-list time,
.memory-message-list footer small { color: var(--muted); font-size: 12px; }
.memory-message-list p { margin: 0; font-size: 14px; line-height: 1.65; white-space: pre-wrap; }
.memory-message-list footer small:first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.memory-message-list .vt-icon-button { width: 44px; height: 44px; }
.memory-fact-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 10px; padding: 18px; }
.memory-fact-list > article { display: grid; align-content: start; gap: 12px; border: 1px solid var(--line); border-radius: 16px; padding: 15px; background: var(--paper); }
.memory-fact-list article > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.memory-fact-list h3 { overflow-wrap: anywhere; margin: 4px 0 0; font-size: 16px; }
.memory-fact-list article > p { margin: 0; font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.memory-fact-list dl { display: grid; gap: 6px; margin: 0; }
.memory-fact-list dl div { display: grid; grid-template-columns: 96px minmax(0,1fr); gap: 9px; }
.memory-fact-list dt,
.memory-fact-list dd { overflow-wrap: anywhere; margin: 0; color: var(--muted); font-size: 12px; }
.memory-fact-list dd { color: var(--ink-2); }
.memory-fact-list footer { display: flex; gap: 6px; margin-top: auto; border-top: 1px solid var(--line); padding-top: 11px; }
.memory-fact-list > :deep(.vt-empty-state),
.memory-fact-list > :deep(.vt-button:last-child),
.memory-fact-list > .inline-error { grid-column: 1 / -1; }
.memory-delete-preview { display: grid; grid-template-columns: 34px minmax(0,1fr); align-items: center; gap: 10px; border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--line)); border-radius: 13px; padding: 12px; color: var(--danger); background: color-mix(in srgb, var(--danger) 7%, var(--paper-strong)); }
.memory-delete-preview p { overflow-wrap: anywhere; margin: 0; color: var(--ink-2); font-size: 14px; line-height: 1.5; }
@media (max-width: 960px) { .memory-toolbar { grid-template-columns: repeat(2,1fr); }.memory-policy-state { grid-column: 1/-1; border-top: 1px solid var(--line); border-left: 0; padding: 12px 0 0; }.memory-summary { grid-template-columns: repeat(2,1fr); } }
@media (max-width: 760px) { .memory-toolbar,.memory-summary,.memory-fact-list { grid-template-columns: 1fr; }.memory-query-error,.memory-tabs { display: grid; align-items: stretch; }.memory-tabs > div { display: grid; grid-template-columns: 1fr 1fr; overflow-x: auto; }.memory-tabs > :deep(.vt-button) { width: 100%; }.memory-message-list > article.assistant,.memory-message-list > article.user { margin: 0; }.memory-message-list article > footer { display: grid; }.memory-fact-list > :deep(.vt-empty-state),.memory-fact-list > :deep(.vt-button:last-child),.memory-fact-list > .inline-error { grid-column: auto; } }
</style>
