<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { ApiError } from "../api/client";
import type { Agent } from "../api/schemas";
import { VtButton, VtDialog, VtField, VtIcon, VtInput, VtSelect } from "./ui";

const props = defineProps<{
  open: boolean;
  agents: Agent[];
  pairDevice: (code: string, name: string, agentId?: string) => Promise<void>;
}>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();

const code = ref("");
const deviceName = ref("");
const agentId = ref("");
const busy = ref(false);
const error = ref("");
const requestId = ref("");
const copied = ref(false);
const publishedAgents = computed(() => props.agents.filter((agent) => agent.publishedVersion > 0));

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    code.value = "";
    deviceName.value = "";
    agentId.value = publishedAgents.value[0]?.id ?? "";
    error.value = "";
    requestId.value = "";
    copied.value = false;
  },
);

function normalizeCode(): void {
  code.value = code.value.replace(/\D/g, "").slice(0, 6);
}

function pairingError(exception: unknown): string {
  if (!(exception instanceof ApiError)) return t("pairing.errors.connection");
  requestId.value = exception.requestId ?? "";
  if (exception.status === 401 || exception.status === 403) return t("pairing.errors.unauthorized");
  if (exception.status === 409) return t("pairing.errors.conflict");
  if (exception.status === 404 || exception.code.includes("expired")) return t("pairing.errors.expired");
  if (exception.status === 400 || exception.code.includes("invalid")) return t("pairing.errors.invalid");
  return t("pairing.errors.connection");
}

async function pair(): Promise<void> {
  normalizeCode();
  if (code.value.length !== 6) {
    error.value = t("pairing.codeError");
    return;
  }
  busy.value = true;
  error.value = "";
  requestId.value = "";
  try {
    const name = deviceName.value.trim() || t("pairing.defaultName", { suffix: code.value.slice(-2) });
    await props.pairDevice(code.value, name, agentId.value || undefined);
    emit("close");
  } catch (exception) {
    error.value = pairingError(exception);
  } finally {
    busy.value = false;
  }
}

async function copyRequestId(): Promise<void> {
  if (!requestId.value) return;
  await navigator.clipboard.writeText(requestId.value);
  copied.value = true;
}
</script>

<template>
  <VtDialog :open="open" :title="t('pairing.title')" :eyebrow="t('pairing.eyebrow')" icon="device" :description="t('pairing.description')" :close-label="t('pairing.close')" width="sm" @close="emit('close')">
    <form id="device-pair-form" class="form-stack" @submit.prevent="pair">
      <div class="pair-dialog-note"><span><VtIcon name="device" :size="19" /></span><div><b>{{ t("pairing.noteTitle") }}</b><p>{{ t("pairing.noteBody") }}</p></div></div>
      <VtField :label="t('pairing.code')" :hint="t('pairing.codeHint')" :error="error" required>
        <VtInput v-model="code" class="pair-code-input" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="284716" @input="normalizeCode" />
      </VtField>
      <button v-if="requestId" class="pair-request-id" type="button" :aria-label="t(copied ? 'pairing.requestIdCopied' : 'pairing.copyRequestId')" @click="copyRequestId">
        <VtIcon :name="copied ? 'check' : 'telemetry'" :size="15" /> {{ t("pairing.requestId", { id: requestId }) }}
      </button>
      <span class="sr-only" aria-live="polite">{{ copied ? t("pairing.requestIdCopied") : "" }}</span>
      <VtField :label="t('pairing.name')" :hint="t('pairing.nameHint')">
        <VtInput v-model="deviceName" maxlength="80" :placeholder="t('pairing.namePlaceholder')" />
      </VtField>
      <VtField :label="t('pairing.agent')">
        <VtSelect v-model="agentId"><option value="">{{ t("pairing.unassigned") }}</option><option v-for="agent in publishedAgents" :key="agent.id" :value="agent.id">{{ agent.name }} · v{{ agent.publishedVersion }}</option></VtSelect>
      </VtField>
    </form>
    <template #footer><VtButton variant="quiet" @click="emit('close')">{{ t("pairing.cancel") }}</VtButton><VtButton form="device-pair-form" type="submit" :busy="busy" :disabled="code.length !== 6"><VtIcon name="plus" :size="17" /> {{ t("pairing.submit") }}</VtButton></template>
  </VtDialog>
</template>
