<script setup lang="ts">
import { computed, ref } from "vue";

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

const selected = ref<Provider>();
const testingId = ref("");
const error = ref("");
const enabled = computed(() => props.providers.filter((provider) => provider.enabled).length);
const healthy = computed(() => props.providers.filter((provider) => provider.enabled && provider.health === "healthy").length);
const kinds = computed(() => new Set(props.providers.map((provider) => provider.kind)).size);
const attention = computed(() => props.providers.filter((provider) => provider.enabled && provider.health !== "healthy").length);
const providerMetrics = computed(() => [
  { label: "Runtime khỏe", value: healthy.value, detail: "Sẵn sàng tham gia routing", tone: "success" as const },
  { label: "Cần kiểm tra", value: attention.value, detail: "Chưa đo hoặc đang degraded", tone: attention.value ? "warning" as const : "neutral" as const },
  { label: "Capabilities", value: kinds.value, detail: "VAD · ASR · LLM · TTS", tone: "info" as const },
]);

async function test(id: string): Promise<void> {
  testingId.value = id;
  error.value = "";
  try { await props.testProvider(id); }
  catch (exception) { error.value = exception instanceof Error ? exception.message : "Provider test thất bại."; }
  finally { testingId.value = ""; }
}
</script>

<template>
  <section class="vt-page" data-page="providers">
    <VtPageHeader eyebrow="AI ROUTING / PROVIDER HUB" title="Một pipeline, nhiều lựa chọn" description="Quản lý local model và OpenAI-compatible provider theo capability, locale, priority, health và circuit breaker." />

    <div class="provider-dashboard">
      <VtOperationsHero
        eyebrow="RUNTIME CONTROL"
        title="Hệ điều phối AI"
        description="Mỗi capability có provider riêng, health probe riêng và circuit breaker độc lập. Provider local được kiểm tra trực tiếp qua readiness của Voice Server."
        :value="enabled"
        value-label="Provider đang bật"
        :value-hint="`${providers.length} cấu hình trong catalog`"
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
    <VtEmptyState v-else icon="resource" title="Chưa cấu hình provider" text="Bootstrap provider trong Manager API để tạo routing chain đầu tiên." />

    <ProviderDialog :open="Boolean(selected)" :provider="selected" :save="updateProvider" @close="selected = undefined" />
  </section>
</template>
