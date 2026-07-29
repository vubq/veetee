<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";

import type { Provider } from "../../api/schemas";
import type { ProviderUpdateInput } from "../../types/manager";
import ProviderCard from "../providers/ProviderCard.vue";
import ProviderDialog from "../providers/ProviderDialog.vue";
import { VtEmptyState, VtMetricStrip, VtOperationsHero, VtPageHeader } from "../ui";

const props = defineProps<{
  providers: Provider[];
  testProvider: (id: string) => Promise<void>;
  updateProvider: (id: string, input: ProviderUpdateInput) => Promise<void>;
}>();
const { t } = useI18n();

const selected = ref<Provider>();
const testingId = ref("");
const error = ref("");
const enabled = computed(() => props.providers.filter((provider) => provider.enabled).length);
const healthy = computed(() => props.providers.filter((provider) => provider.enabled && provider.health === "healthy").length);
const kinds = computed(() => new Set(props.providers.map((provider) => provider.kind)).size);
const attention = computed(() => props.providers.filter((provider) => provider.enabled && provider.health !== "healthy").length);
const providerMetrics = computed(() => [
  { label: t("providers.metrics.healthy"), value: healthy.value, detail: t("providers.metrics.healthyDetail"), tone: "success" as const },
  { label: t("providers.metrics.attention"), value: attention.value, detail: t("providers.metrics.attentionDetail"), tone: attention.value ? "warning" as const : "neutral" as const },
  { label: t("providers.metrics.capabilities"), value: kinds.value, detail: t("providers.metrics.capabilityKinds"), tone: "info" as const },
]);

async function test(id: string): Promise<void> {
  testingId.value = id;
  error.value = "";
  try { await props.testProvider(id); }
  catch (exception) { error.value = exception instanceof Error ? exception.message : t("providers.errors.testFailed"); }
  finally { testingId.value = ""; }
}
</script>

<template>
  <section class="vt-page" data-page="providers">
    <VtPageHeader :eyebrow="t('pages.providers.eyebrow')" :title="t('pages.providers.title')" :description="t('pages.providers.description')" />

    <div class="provider-dashboard" data-page-section="summary">
      <VtOperationsHero
        :eyebrow="t('providers.hero.eyebrow')"
        :title="t('providers.hero.title')"
        :description="t('providers.hero.description')"
        :value="enabled"
        :value-label="t('providers.hero.enabled')"
        :value-hint="t('providers.hero.catalogCount', { count: providers.length })"
        icon="provider"
      />
      <VtMetricStrip :items="providerMetrics" />
    </div>
    <p v-if="error" class="inline-error page-error" role="alert">{{ error }}</p>

    <div v-if="providers.length" class="provider-grid">
      <ProviderCard
        v-for="provider in providers"
        :key="provider.id"
        :provider="provider"
        :testing="testingId === provider.id"
        @test="test(provider.id)"
        @edit="selected = provider"
      />
    </div>
    <VtEmptyState v-else icon="resource" :title="t('providers.empty.title')" :text="t('providers.empty.body')" />

    <ProviderDialog :open="Boolean(selected)" :provider="selected" :save="updateProvider" @close="selected = undefined" />
  </section>
</template>
