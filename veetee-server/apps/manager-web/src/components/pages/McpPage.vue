<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import type { Device, McpTool } from "../../api/schemas";
import { VtBadge, VtButton, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect, VtTextarea } from "../ui";

const props = defineProps<{
  devices: Device[];
  tools: McpTool[];
  toolsLive: boolean;
  selectedDeviceId: string;
  embedded?: boolean;
  callTool: (deviceId: string, name: string, argumentsValue: Record<string, unknown>, confirmed: boolean) => Promise<Record<string, unknown>>;
}>();
const emit = defineEmits<{ selectDevice: [id: string] }>();
const { t } = useI18n();

const selectedName = ref("");
const values = reactive<Record<string, string | boolean>>({});
const confirmed = ref(false);
const busy = ref(false);
const error = ref("");
const result = ref<Record<string, unknown>>();

const selected = computed(() => props.tools.find((tool) => tool.name === selectedName.value) ?? props.tools[0]);
const schema = computed(() => selected.value?.inputSchema ?? {});
const properties = computed(() => {
  const raw = schema.value.properties;
  return raw && typeof raw === "object" && !Array.isArray(raw) ? Object.entries(raw as Record<string, Record<string, unknown>>) : [];
});
const required = computed(() => Array.isArray(schema.value.required) ? schema.value.required.map(String) : []);

watch(
  selected,
  (tool) => {
    if (!tool) return;
    selectedName.value = tool.name;
    for (const key of Object.keys(values)) delete values[key];
    for (const [name, definition] of Object.entries((tool.inputSchema.properties as Record<string, Record<string, unknown>> | undefined) ?? {})) {
      values[name] = definition.type === "boolean" ? Boolean(definition.default) : String(definition.default ?? "");
    }
    confirmed.value = false;
    error.value = "";
    result.value = undefined;
  },
  { immediate: true },
);

function inputType(definition: Record<string, unknown>): string {
  return definition.type === "integer" || definition.type === "number" ? "number" : "text";
}

function parseValue(value: string | boolean, definition: Record<string, unknown>): unknown {
  if (definition.type === "boolean") return Boolean(value);
  if (definition.type === "integer") return Number.parseInt(String(value), 10);
  if (definition.type === "number") return Number(value);
  if (definition.type === "array" || definition.type === "object") return JSON.parse(String(value));
  return String(value);
}

async function run(): Promise<void> {
  if (!selected.value || !props.selectedDeviceId) return;
  if (selected.value.requiresConfirmation && !confirmed.value) {
    error.value = t("mcp.errors.confirmationRequired");
    return;
  }
  busy.value = true;
  error.value = "";
  result.value = undefined;
  try {
    const argumentsValue = Object.fromEntries(properties.value.map(([name, definition]) => [name, parseValue(values[name] ?? "", definition)]));
    result.value = await props.callTool(props.selectedDeviceId, selected.value.name, argumentsValue, confirmed.value);
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("mcp.errors.callFailed");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="vt-page" :class="{ 'is-embedded': embedded }" data-page="mcp">
    <VtPageHeader v-if="!embedded" :eyebrow="t('mcp.eyebrow')" :title="t('mcp.title')" :description="t('mcp.description')" />
    <header v-else class="device-subpage-header"><span class="vt-kicker">{{ t("mcp.eyebrow") }}</span><h2>{{ t("mcp.embeddedTitle", { device: devices.find((device) => device.id === selectedDeviceId)?.name }) }}</h2><p>{{ t("mcp.embeddedDescription") }}</p></header>

    <div class="mcp-toolbar" :class="{ 'is-embedded': embedded }">
      <VtField v-if="!embedded" :label="t('mcp.device')">
        <VtSelect :model-value="selectedDeviceId" @update:model-value="emit('selectDevice', String($event))"><option value="">{{ t("mcp.noDevice") }}</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.status }}</option></VtSelect>
      </VtField>
      <div><VtBadge :tone="toolsLive ? 'success' : 'warning'" dot>{{ t(toolsLive ? "mcp.liveCatalog" : "mcp.baselineCatalog") }}</VtBadge><p>{{ t(toolsLive ? "mcp.liveDescription" : "mcp.baselineDescription") }}</p></div>
    </div>

    <div v-if="tools.length" class="mcp-layout">
      <aside class="tool-list">
        <button v-for="tool in tools" :key="tool.name" type="button" :class="{ active: selected?.name === tool.name }" @click="selectedName = tool.name">
          <span><VtIcon name="tool" :size="18" /></span><div><b>{{ tool.name }}</b><small>{{ t(tool.audience === "regular" ? "mcp.aiCallable" : "mcp.userOnly") }}</small></div><i :class="tool.safetyClass"></i>
        </button>
      </aside>

      <article v-if="selected" class="vt-panel tool-detail">
        <header class="tool-detail-header"><div><span class="vt-kicker">{{ t(selected.audience === "regular" ? "mcp.aiCallableUpper" : "mcp.userOnlyUpper") }}</span><h2>{{ selected.name }}</h2><p>{{ selected.description }}</p></div><div><VtBadge :tone="selected.safetyClass === 'destructive' ? 'danger' : selected.safetyClass === 'disruptive' ? 'warning' : 'info'">{{ selected.safetyClass }}</VtBadge><VtBadge v-if="selected.requiresConfirmation" tone="danger">{{ t("mcp.confirmationRequired") }}</VtBadge></div></header>

        <form class="tool-form" @submit.prevent="run">
          <div v-if="properties.length" class="form-grid two">
            <VtField v-for="([name, definition]) in properties" :key="name" :label="name" :hint="String(definition.description ?? definition.type ?? '')" :required="required.includes(name)">
              <label v-if="definition.type === 'boolean'" class="switch-control"><input v-model="values[name]" type="checkbox" /><span></span><b>{{ t(values[name] ? "common.on" : "common.off") }}</b></label>
              <VtTextarea v-else-if="definition.type === 'object' || definition.type === 'array'" v-model="values[name] as string" :placeholder="definition.type === 'array' ? '[]' : '{}'" :required="required.includes(name)" />
              <VtInput v-else v-model="values[name] as string" :type="inputType(definition)" :min="definition.minimum as number" :max="definition.maximum as number" :required="required.includes(name)" />
            </VtField>
          </div>
          <p v-else class="no-arguments">{{ t("mcp.noArguments") }}</p>
          <label v-if="selected.requiresConfirmation" class="confirmation-box"><input v-model="confirmed" type="checkbox" /><span><VtIcon name="warning" :size="19" /></span><div><b>{{ t("mcp.confirmTitle") }}</b><small>{{ t("mcp.confirmDescription") }}</small></div></label>
          <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
          <VtButton type="submit" :busy="busy" :disabled="!selectedDeviceId || !toolsLive"><VtIcon name="play" :size="16" /> {{ t(selectedDeviceId && toolsLive ? "mcp.run" : "mcp.noLiveCatalog") }}</VtButton>
        </form>

        <div v-if="result" class="tool-result"><span class="vt-kicker">{{ t("mcp.callResult") }}</span><pre>{{ JSON.stringify(result, null, 2) }}</pre></div>
        <details class="schema-details"><summary>{{ t("mcp.inputSchema") }} <VtIcon name="chevron" :size="15" /></summary><pre>{{ JSON.stringify(selected.inputSchema, null, 2) }}</pre></details>
      </article>
    </div>
    <VtEmptyState v-else icon="tool" :title="t('mcp.emptyTitle')" :text="t('mcp.emptyBody')" />
  </section>
</template>
