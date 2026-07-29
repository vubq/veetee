<script setup lang="ts">
import { useI18n } from "vue-i18n";

import type { Provider } from "../../api/schemas";
import { formatDate, statusTone } from "../../utils/format";
import { VtBadge, VtButton, VtIcon } from "../ui";

defineProps<{
  provider: Provider;
  testing: boolean;
}>();

const emit = defineEmits<{
  test: [];
  edit: [];
}>();
const { t } = useI18n();

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

const errorMessages: Record<string, string> = {
  runtime_probe_unavailable: "providerCard.errors.runtimeProbeUnavailable",
  voice_runtime_unreachable: "providerCard.errors.voiceRuntimeUnreachable",
  runtime_component_unreported: "providerCard.errors.runtimeComponentUnreported",
  runtime_component_unhealthy: "providerCard.errors.runtimeComponentUnhealthy",
  timeout: "providerCard.errors.timeout",
  unreachable: "providerCard.errors.unreachable",
};

function healthDescription(provider: Provider): string {
  if (!provider.enabled) return t("providerCard.health.disabled");
  if (provider.health === "healthy") return t("providerCard.health.ready");
  if (provider.healthErrorCode?.startsWith("http_")) {
    return t("providerCard.health.httpError", { status: provider.healthErrorCode.slice(5) });
  }
  if (provider.healthErrorCode) return t(errorMessages[provider.healthErrorCode] ?? "providerCard.health.probeFailed");
  return t(provider.health === "unknown" ? "providerCard.health.notChecked" : "providerCard.health.degraded");
}

function circuitLabel(value: Provider["circuitState"]): string {
  return t(`providerCard.circuit.${value}`);
}

function authLabel(provider: Provider): string {
  if (!provider.baseUrl) return t("providerCard.auth.inProcess");
  return t(provider.secretConfigured ? "providerCard.auth.bearerSecret" : "providerCard.auth.noSecret");
}
</script>

<template>
  <article class="provider-card" :class="{ 'is-disabled': !provider.enabled }" :data-provider-kind="provider.kind">
    <header>
      <span class="provider-kind-icon"><VtIcon name="provider" :size="20" /></span>
      <div class="provider-identity">
        <span class="vt-kicker">{{ t(kindLabels[provider.kind]) }} · P{{ provider.priority }}</span>
        <h2>{{ provider.model }}</h2>
        <p>{{ provider.adapter }}</p>
      </div>
      <div class="provider-badges">
        <VtBadge :tone="provider.enabled ? 'info' : 'neutral'">{{ t(provider.enabled ? "providerCommon.enabled" : "providerCommon.disabled") }}</VtBadge>
        <VtBadge :tone="statusTone(provider.health)" dot>{{ t(healthLabels[provider.health]) }}</VtBadge>
      </div>
    </header>

    <div class="provider-runtime">
      <span><VtIcon :name="provider.baseUrl ? 'telemetry' : 'device'" :size="16" /></span>
      <div>
        <small>{{ t(provider.baseUrl ? "providerCard.runtime.http" : "providerCard.runtime.voiceServer") }}</small>
        <code>{{ provider.baseUrl ?? t("providerCard.runtime.inProcessReady") }}</code>
      </div>
    </div>

    <dl class="provider-facts">
      <div><dt :title="t('providerCard.facts.probeHint')">{{ t("providerCard.facts.probe") }}</dt><dd>{{ provider.healthLatencyMs !== undefined ? `${provider.healthLatencyMs} ms` : t("providerCommon.health.unknown") }}</dd></div>
      <div><dt>{{ t("providerCard.facts.circuit") }}</dt><dd>{{ circuitLabel(provider.circuitState) }}</dd></div>
      <div><dt>{{ t("providerCard.facts.languages") }}</dt><dd>{{ provider.locales.join(", ") || "—" }}</dd></div>
      <div><dt>{{ t("providerCard.facts.auth") }}</dt><dd>{{ authLabel(provider) }}</dd></div>
    </dl>

    <div class="provider-health-note" :class="`is-${provider.health}`">
      <span><VtIcon :name="provider.health === 'healthy' ? 'check' : 'warning'" :size="16" /></span>
      <div><b>{{ t(healthLabels[provider.health]) }}</b><p>{{ healthDescription(provider) }}</p></div>
    </div>

    <footer>
      <small>{{ provider.healthCheckedAt ? t("providerCard.checkedAt", { date: formatDate(provider.healthCheckedAt) }) : t("providerCard.notChecked") }}</small>
      <div>
        <VtButton size="sm" variant="secondary" :busy="testing" @click="emit('test')"><VtIcon name="refresh" :size="15" /> {{ t("providerCard.actions.test") }}</VtButton>
        <VtButton size="sm" variant="quiet" @click="emit('edit')"><VtIcon name="edit" :size="15" /> {{ t("providerCard.actions.configure") }}</VtButton>
      </div>
    </footer>
  </article>
</template>
