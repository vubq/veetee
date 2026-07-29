<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import type { Provider } from "../../api/schemas";
import type { ProviderUpdateInput } from "../../types/manager";
import { statusTone } from "../../utils/format";
import { VtBadge, VtButton, VtDialog, VtField, VtIcon, VtInput, VtSelect, VtSwitch } from "../ui";

const props = defineProps<{
  open: boolean;
  provider: Provider | undefined;
  save: (id: string, input: ProviderUpdateInput) => Promise<void>;
}>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();

const form = reactive({
  adapter: "", model: "", baseUrl: "", enabled: true, priority: 10, locales: "vi-VN",
  secretAction: "keep" as "keep" | "rotate" | "clear", secret: "",
  temperature: 0.2, topP: 0.95, maxCompletionTokens: 1024,
  serviceTier: "on_demand", reasoningEffort: "none", streamProseResponse: true,
  parallelToolCalls: true, responseFormat: "auto", completionTokenParameter: "max_tokens",
  voice: "", style: "tu_nhien", rate: 1, pitchHz: 0, volume: 1, outputSampleRate: 24000,
});
const busy = ref(false);
const error = ref("");
const kindLabels: Record<Provider["kind"], string> = {
  vad: "providerCommon.kinds.vad",
  asr: "providerCommon.kinds.asr",
  llm: "providerCommon.kinds.llm",
  tts: "providerCommon.kinds.tts",
  realtime: "providerCommon.kinds.realtime",
  memory: "providerCommon.kinds.memory",
};
const healthLabels: Record<Provider["health"], string> = {
  healthy: "providerCommon.health.healthy",
  degraded: "providerCommon.health.degraded",
  unknown: "providerCommon.health.unknown",
};
const dialogTitle = computed(() => props.provider ? t("providerDialog.titleKind", { kind: t(kindLabels[props.provider.kind]) }) : t("providerDialog.title"));
const isLlm = computed(() => props.provider?.kind === "llm");
const isGroq = computed(() => form.adapter.toLowerCase().includes("groq"));
const isTts = computed(() => props.provider?.kind === "tts");
const isVieNeu = computed(() => form.adapter.toLowerCase().includes("vieneu"));
const supportsPitch = computed(() => props.provider?.config?.supportsPitch !== false);
const ttsQualityWarnings = computed(() => {
  if (!isTts.value || !isVieNeu.value) return [];
  const warnings: string[] = [];
  if (Number(form.rate) > 1.2) {
    warnings.push(t("providerDialog.warnings.rate"));
  }
  if (Number(form.volume) > 1) {
    warnings.push(t("providerDialog.warnings.volume"));
  }
  return warnings;
});

watch(
  () => [props.open, props.provider] as const,
  () => {
    if (!props.open || !props.provider) return;
    form.adapter = props.provider.adapter;
    form.model = props.provider.model;
    form.baseUrl = props.provider.baseUrl ?? "";
    form.enabled = props.provider.enabled;
    form.priority = props.provider.priority;
    form.locales = props.provider.locales.join(", ");
    form.secretAction = "keep";
    form.secret = "";
    const config = props.provider.config ?? {};
    form.temperature = Number(config.temperature ?? 0.2);
    form.topP = Number(config.topP ?? 0.95);
    form.maxCompletionTokens = Number(config.maxCompletionTokens ?? 1024);
    form.serviceTier = String(config.serviceTier ?? "on_demand");
    form.reasoningEffort = String(config.reasoningEffort ?? "none");
    form.streamProseResponse = config.streamProseResponse !== false;
    form.parallelToolCalls = config.parallelToolCalls !== false;
    form.responseFormat = String(config.responseFormat ?? "auto");
    form.completionTokenParameter = String(config.completionTokenParameter ?? "max_tokens");
    form.voice = String(config.voice ?? config.voiceId ?? "");
    form.style = String(config.style ?? (isVieNeu.value ? "tu_nhien" : ""));
    form.rate = Number(config.rate ?? 1);
    form.pitchHz = Number(config.pitchHz ?? 0);
    form.volume = Number(config.volume ?? 1);
    form.outputSampleRate = Number(config.outputSampleRate ?? 24000);
    error.value = "";
  },
  { immediate: true },
);

async function submit(): Promise<void> {
  if (!props.provider) return;
  if (form.secretAction === "rotate" && !form.secret) {
    error.value = t("providerDialog.errors.secretRequired");
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const config = { ...(props.provider.config ?? {}) } as Record<string, unknown>;
    if (isLlm.value) {
      Object.assign(config, {
        temperature: Number(form.temperature),
        topP: Number(form.topP),
        maxCompletionTokens: Number(form.maxCompletionTokens),
        reasoningEffort: form.reasoningEffort,
        parallelToolCalls: form.parallelToolCalls,
        streamProseResponse: form.streamProseResponse,
        responseFormat: form.responseFormat,
        completionTokenParameter: form.completionTokenParameter,
        ...(isGroq.value ? { serviceTier: form.serviceTier } : {}),
      });
      if (!isGroq.value) delete config.serviceTier;
    }
    if (isTts.value) {
      Object.assign(config, {
        voice: form.voice.trim() || undefined,
        ...(isVieNeu.value ? { style: form.style } : {}),
        rate: Number(form.rate),
        pitchHz: Number(form.pitchHz),
        volume: Number(form.volume),
        outputSampleRate: Number(form.outputSampleRate),
      });
    }
    await props.save(props.provider.id, {
      adapter: form.adapter.trim(), model: form.model.trim(), baseUrl: form.baseUrl.trim() || null,
      enabled: form.enabled, priority: Number(form.priority),
      locales: form.locales.split(",").map((value) => value.trim()).filter(Boolean),
      secretAction: form.secretAction,
      ...(form.secretAction === "rotate" ? { secret: form.secret } : {}),
      ...(isLlm.value || isTts.value ? { config } : {}),
    });
    emit("close");
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("providerDialog.errors.saveFailed");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <VtDialog :open="open" :title="dialogTitle" :eyebrow="t('providerDialog.eyebrow')" icon="provider" :description="t('providerDialog.description')" width="lg" @close="emit('close')">
    <form v-if="provider" id="provider-config-form" class="provider-dialog-form" @submit.prevent="submit">
      <section class="provider-dialog-context">
        <span><VtIcon name="provider" :size="20" /></span>
        <div><small>{{ t(kindLabels[provider.kind]) }}</small><b>{{ provider.model }}</b><code>{{ provider.adapter }}</code></div>
        <div><VtBadge :tone="provider.enabled ? 'info' : 'neutral'">{{ t(provider.enabled ? "providerCommon.enabled" : "providerCommon.disabled") }}</VtBadge><VtBadge :tone="statusTone(provider.health)" dot>{{ t(healthLabels[provider.health]) }}</VtBadge></div>
      </section>

      <section class="provider-form-section">
        <header><span>01</span><div><h3>{{ t("providerDialog.runtime.title") }}</h3><p>{{ t("providerDialog.runtime.description") }}</p></div></header>
        <div class="form-grid two">
          <VtField :label="t('providerDialog.runtime.adapter')" required><VtInput v-model="form.adapter" maxlength="120" required /></VtField>
          <VtField :label="t('providerDialog.runtime.model')" required><VtInput v-model="form.model" maxlength="200" required /></VtField>
          <VtField :label="t('providerDialog.runtime.baseUrl')" :hint="t('providerDialog.runtime.baseUrlHint')" class="span-two"><VtInput v-model="form.baseUrl" placeholder="http://127.0.0.1:8317/v1" /></VtField>
        </div>
      </section>

      <section class="provider-form-section">
        <header><span>02</span><div><h3>{{ t("providerDialog.routing.title") }}</h3><p>{{ t("providerDialog.routing.description") }}</p></div></header>
        <div class="form-grid two">
          <VtField :label="t('providerDialog.routing.priority')" :hint="t('providerDialog.routing.priorityHint')"><VtInput v-model="form.priority" type="number" min="0" max="1000" /></VtField>
          <VtField :label="t('providerDialog.routing.locales')" :hint="t('providerDialog.routing.localesHint')"><VtInput v-model="form.locales" placeholder="vi-VN, en-US" /></VtField>
          <div class="provider-switch span-two"><VtSwitch v-model="form.enabled" :label="t('providerDialog.routing.enabled')" :description="t('providerDialog.routing.enabledDescription')" /></div>
        </div>
      </section>

      <section v-if="isLlm || isTts" class="provider-form-section">
        <header><span>03</span><div><h3>{{ t("providerDialog.parameters.title") }}</h3><p>{{ t("providerDialog.parameters.description") }}</p></div></header>
        <div v-if="isLlm" class="form-grid two">
          <VtField :label="t('providerDialog.parameters.temperature')" :hint="t('providerDialog.parameters.temperatureRange')"><VtInput v-model="form.temperature" type="number" min="0" max="2" step="0.05" /></VtField>
          <VtField :label="t('providerDialog.parameters.topP')" :hint="t('providerDialog.parameters.topPRange')"><VtInput v-model="form.topP" type="number" min="0" max="1" step="0.05" /></VtField>
          <VtField :label="t('providerDialog.parameters.maxTokens')" :hint="t('providerDialog.parameters.maxTokensRange')"><VtInput v-model="form.maxCompletionTokens" type="number" min="64" max="16384" /></VtField>
          <VtField v-if="isGroq" :label="t('providerDialog.parameters.serviceTier')"><VtSelect v-model="form.serviceTier"><option value="on_demand">{{ t("providerDialog.options.onDemand") }}</option><option value="auto">{{ t("providerDialog.options.auto") }}</option><option value="flex">{{ t("providerDialog.options.flex") }}</option><option value="performance">{{ t("providerDialog.options.performance") }}</option></VtSelect></VtField>
          <VtField :label="t('providerDialog.parameters.reasoningEffort')"><VtSelect v-model="form.reasoningEffort"><option value="none">{{ t("providerDialog.options.none") }}</option><option value="default">{{ t("providerDialog.options.default") }}</option><option value="low">{{ t("providerDialog.options.low") }}</option><option value="medium">{{ t("providerDialog.options.medium") }}</option><option value="high">{{ t("providerDialog.options.high") }}</option></VtSelect></VtField>
          <VtField :label="t('providerDialog.parameters.structuredResponse')"><VtSelect v-model="form.responseFormat"><option value="auto">{{ t("providerDialog.options.auto") }}</option><option value="json_schema">{{ t("providerDialog.options.strictJsonSchema") }}</option><option value="json_object">{{ t("providerDialog.options.jsonObject") }}</option></VtSelect></VtField>
          <VtField :label="t('providerDialog.parameters.tokenParameter')"><VtSelect v-model="form.completionTokenParameter"><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option></VtSelect></VtField>
          <div class="provider-switch span-two"><VtSwitch v-model="form.parallelToolCalls" :label="t('providerDialog.parameters.parallelTools')" :description="t('providerDialog.parameters.parallelToolsDescription')" /></div>
          <div class="provider-switch span-two"><VtSwitch v-model="form.streamProseResponse" :label="t('providerDialog.parameters.streamToTts')" :description="t('providerDialog.parameters.streamToTtsDescription')" /></div>
        </div>
        <div v-else class="form-grid two">
          <VtField :label="t('providerDialog.tts.defaultVoice')" :hint="t('providerDialog.tts.defaultVoiceHint')"><VtInput v-model="form.voice" placeholder="Trúc Ly" /></VtField>
          <VtField v-if="isVieNeu" :label="t('providerDialog.tts.defaultStyle')"><VtSelect v-model="form.style"><option value="tu_nhien">{{ t("providerDialog.tts.natural") }}</option><option value="doc_truyen">{{ t("providerDialog.tts.story") }}</option><option value="tin_tuc">{{ t("providerDialog.tts.news") }}</option></VtSelect></VtField>
          <VtField :label="t('providerDialog.tts.rate')" :hint="t('providerDialog.tts.rateHint')"><VtInput v-model="form.rate" type="number" min="0.5" max="2" step="0.05" /></VtField>
          <VtField v-if="supportsPitch" :label="t('providerDialog.tts.pitch')"><VtInput v-model="form.pitchHz" type="number" min="-100" max="100" /></VtField>
          <VtField :label="t('providerDialog.tts.volume')" :hint="t('providerDialog.tts.volumeRange')"><VtInput v-model="form.volume" type="number" min="0" max="1.5" step="0.05" /></VtField>
          <VtField :label="t('providerDialog.tts.sampleRate')" :hint="t('providerDialog.tts.sampleRateRange')"><VtInput v-model="form.outputSampleRate" type="number" min="8000" max="48000" step="1000" /></VtField>
        </div>
        <div v-if="ttsQualityWarnings.length" class="provider-tts-quality-warning" role="status">
          <VtIcon name="warning" :size="17" />
          <div><b>{{ t("providerDialog.warnings.benchmark") }}</b><p v-for="warning in ttsQualityWarnings" :key="warning">{{ warning }}</p></div>
        </div>
      </section>

      <section class="provider-form-section">
        <header><span>04</span><div><h3>{{ t("providerDialog.credential.title") }}</h3><p>{{ t("providerDialog.credential.description") }}</p></div></header>
        <div class="form-grid two">
          <VtField :label="t('providerDialog.credential.action')"><VtSelect v-model="form.secretAction" name="secretAction"><option value="keep">{{ t("providerDialog.credential.keep") }}</option><option value="rotate">{{ t("providerDialog.credential.rotate") }}</option><option value="clear">{{ t("providerDialog.credential.clear") }}</option></VtSelect></VtField>
          <VtField v-if="form.secretAction === 'rotate'" :label="t('providerDialog.credential.newSecret')" :error="error" required><VtInput v-model="form.secret" type="password" autocomplete="new-password" /></VtField>
          <div v-else class="provider-secret-note"><VtIcon name="check" :size="17" /><span><b>{{ t(form.secretAction === "clear" ? "providerDialog.credential.willClear" : "providerDialog.credential.willKeep") }}</b><small>{{ t("providerDialog.credential.browserNote") }}</small></span></div>
        </div>
      </section>
      <p v-if="error && form.secretAction !== 'rotate'" class="inline-error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <div class="dialog-action-layout">
        <span><VtIcon name="warning" :size="16" /> {{ t("providerDialog.footer.healthReset") }}</span>
        <div><VtButton variant="quiet" @click="emit('close')">{{ t("common.cancel") }}</VtButton><VtButton form="provider-config-form" type="submit" :busy="busy">{{ t("providerDialog.footer.save") }}</VtButton></div>
      </div>
    </template>
  </VtDialog>
</template>
