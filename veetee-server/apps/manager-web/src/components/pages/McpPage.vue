<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

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
    error.value = "Tool này cần xác nhận rõ ràng trước khi gọi.";
    return;
  }
  busy.value = true;
  error.value = "";
  result.value = undefined;
  try {
    const argumentsValue = Object.fromEntries(properties.value.map(([name, definition]) => [name, parseValue(values[name] ?? "", definition)]));
    result.value = await props.callTool(props.selectedDeviceId, selected.value.name, argumentsValue, confirmed.value);
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "MCP tool call thất bại.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="vt-page" :class="{ 'is-embedded': embedded }" data-page="mcp">
    <VtPageHeader v-if="!embedded" eyebrow="MODEL CONTEXT PROTOCOL / TOOLS" title="Khả năng của robot" description="Catalog live từ chính thiết bị quyết định tool nào có thể gọi. Safety class và confirmation được kiểm tra lại ở server." />
    <header v-else class="device-subpage-header"><span class="vt-kicker">MODEL CONTEXT PROTOCOL / TOOLS</span><h2>MCP live của {{ devices.find((device) => device.id === selectedDeviceId)?.name }}</h2><p>Chỉ tool do chính thiết bị đang kết nối report mới được phép gọi.</p></header>

    <div class="mcp-toolbar" :class="{ 'is-embedded': embedded }">
      <VtField v-if="!embedded" label="Thiết bị thực thi">
        <VtSelect :model-value="selectedDeviceId" @update:model-value="emit('selectDevice', String($event))"><option value="">Chưa có thiết bị</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.status }}</option></VtSelect>
      </VtField>
      <div><VtBadge :tone="toolsLive ? 'success' : 'warning'" dot>{{ toolsLive ? "Live device catalog" : "Baseline capability" }}</VtBadge><p>{{ toolsLive ? "Schema được báo bởi voice session đang hoạt động." : "Chỉ để tham khảo; không cho gọi trên thiết bị." }}</p></div>
    </div>

    <div v-if="tools.length" class="mcp-layout">
      <aside class="tool-list">
        <button v-for="tool in tools" :key="tool.name" type="button" :class="{ active: selected?.name === tool.name }" @click="selectedName = tool.name">
          <span><VtIcon name="tool" :size="18" /></span><div><b>{{ tool.name }}</b><small>{{ tool.audience === "regular" ? "AI-callable" : "User-only" }}</small></div><i :class="tool.safetyClass"></i>
        </button>
      </aside>

      <article v-if="selected" class="vt-panel tool-detail">
        <header class="tool-detail-header"><div><span class="vt-kicker">{{ selected.audience === "regular" ? "AI-CALLABLE" : "USER-ONLY" }}</span><h2>{{ selected.name }}</h2><p>{{ selected.description }}</p></div><div><VtBadge :tone="selected.safetyClass === 'destructive' ? 'danger' : selected.safetyClass === 'disruptive' ? 'warning' : 'info'">{{ selected.safetyClass }}</VtBadge><VtBadge v-if="selected.requiresConfirmation" tone="danger">Cần xác nhận</VtBadge></div></header>

        <form class="tool-form" @submit.prevent="run">
          <div v-if="properties.length" class="form-grid two">
            <VtField v-for="([name, definition]) in properties" :key="name" :label="name" :hint="String(definition.description ?? definition.type ?? '')" :required="required.includes(name)">
              <label v-if="definition.type === 'boolean'" class="switch-control"><input v-model="values[name]" type="checkbox" /><span></span><b>{{ values[name] ? "Bật" : "Tắt" }}</b></label>
              <VtTextarea v-else-if="definition.type === 'object' || definition.type === 'array'" v-model="values[name] as string" :placeholder="definition.type === 'array' ? '[]' : '{}'" :required="required.includes(name)" />
              <VtInput v-else v-model="values[name] as string" :type="inputType(definition)" :min="definition.minimum as number" :max="definition.maximum as number" :required="required.includes(name)" />
            </VtField>
          </div>
          <p v-else class="no-arguments">Tool này không cần tham số.</p>
          <label v-if="selected.requiresConfirmation" class="confirmation-box"><input v-model="confirmed" type="checkbox" /><span><VtIcon name="warning" :size="19" /></span><div><b>Tôi xác nhận thực hiện hành động này</b><small>Thao tác có thể ảnh hưởng trạng thái vật lý hoặc dữ liệu trên thiết bị.</small></div></label>
          <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
          <VtButton type="submit" :busy="busy" :disabled="!selectedDeviceId || !toolsLive"><VtIcon name="play" :size="16" /> {{ selectedDeviceId && toolsLive ? "Chạy trên thiết bị" : "Chưa có catalog live" }}</VtButton>
        </form>

        <div v-if="result" class="tool-result"><span class="vt-kicker">CALL RESULT</span><pre>{{ JSON.stringify(result, null, 2) }}</pre></div>
        <details class="schema-details"><summary>Input JSON Schema <VtIcon name="chevron" :size="15" /></summary><pre>{{ JSON.stringify(selected.inputSchema, null, 2) }}</pre></details>
      </article>
    </div>
    <VtEmptyState v-else icon="tool" title="Chưa có MCP tool" text="Kết nối thiết bị hoặc kiểm tra baseline capability từ Manager API." />
  </section>
</template>
