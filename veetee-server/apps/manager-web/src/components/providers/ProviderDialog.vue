<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

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

const form = reactive({
  adapter: "", model: "", baseUrl: "", enabled: true, priority: 10, locales: "vi-VN",
  secretAction: "keep" as "keep" | "rotate" | "clear", secret: "",
  temperature: 0.2, topP: 0.95, maxCompletionTokens: 1024,
  serviceTier: "on_demand", reasoningEffort: "none", streamProseResponse: true,
  voice: "", rate: 1, pitchHz: 0, volume: 1, outputSampleRate: 24000,
});
const busy = ref(false);
const error = ref("");
const kindLabels: Record<Provider["kind"], string> = {
  vad: "Phát hiện giọng nói",
  asr: "Nhận dạng tiếng nói",
  llm: "Mô hình ngôn ngữ",
  tts: "Tổng hợp giọng nói",
  realtime: "Realtime speech",
  memory: "Bộ nhớ",
};
const healthLabels: Record<Provider["health"], string> = {
  healthy: "Khỏe",
  degraded: "Cần kiểm tra",
  unknown: "Chưa đo",
};
const dialogTitle = computed(() => props.provider ? `Cấu hình ${props.provider.kind.toUpperCase()}` : "Cấu hình provider");
const isGroq = computed(() => props.provider?.adapter.toLowerCase().includes("groq") ?? false);
const isTts = computed(() => props.provider?.kind === "tts");
const supportsPitch = computed(() => props.provider?.config?.supportsPitch !== false);

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
    form.voice = String(config.voice ?? config.voiceId ?? "");
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
    error.value = "Hãy nhập secret mới hoặc chọn giữ nguyên.";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const config = { ...(props.provider.config ?? {}) } as Record<string, unknown>;
    if (isGroq.value) {
      Object.assign(config, {
        temperature: Number(form.temperature),
        topP: Number(form.topP),
        maxCompletionTokens: Number(form.maxCompletionTokens),
        serviceTier: form.serviceTier,
        reasoningEffort: form.reasoningEffort,
        streamProseResponse: form.streamProseResponse,
      });
    }
    if (isTts.value) {
      Object.assign(config, {
        voice: form.voice.trim() || undefined,
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
      ...(isGroq.value || isTts.value ? { config } : {}),
    });
    emit("close");
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "Không thể lưu provider.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <VtDialog :open="open" :title="dialogTitle" eyebrow="AI ROUTING / PROVIDER" icon="provider" description="Thay đổi binding runtime, policy route và secret mà không làm lộ credential hiện tại." width="lg" @close="emit('close')">
    <form v-if="provider" id="provider-config-form" class="provider-dialog-form" @submit.prevent="submit">
      <section class="provider-dialog-context">
        <span><VtIcon name="provider" :size="20" /></span>
        <div><small>{{ kindLabels[provider.kind] }}</small><b>{{ provider.model }}</b><code>{{ provider.adapter }}</code></div>
        <div><VtBadge :tone="provider.enabled ? 'info' : 'neutral'">{{ provider.enabled ? "Đang bật" : "Đã tắt" }}</VtBadge><VtBadge :tone="statusTone(provider.health)" dot>{{ healthLabels[provider.health] }}</VtBadge></div>
      </section>

      <section class="provider-form-section">
        <header><span>01</span><div><h3>Runtime binding</h3><p>Chọn adapter, model và endpoint mà Voice Server sẽ sử dụng.</p></div></header>
        <div class="form-grid two">
          <VtField label="Adapter" required><VtInput v-model="form.adapter" maxlength="120" required /></VtField>
          <VtField label="Model" required><VtInput v-model="form.model" maxlength="200" required /></VtField>
          <VtField label="Base URL" hint="Để trống cho provider chạy in-process trong Voice Server" class="span-two"><VtInput v-model="form.baseUrl" placeholder="http://127.0.0.1:20128/v1" /></VtField>
        </div>
      </section>

      <section class="provider-form-section">
        <header><span>02</span><div><h3>Routing policy</h3><p>Priority nhỏ hơn được ưu tiên trước trong provider chain cùng capability.</p></div></header>
        <div class="form-grid two">
          <VtField label="Priority" hint="0 là ưu tiên cao nhất"><VtInput v-model="form.priority" type="number" min="0" max="1000" /></VtField>
          <VtField label="Locales" hint="Danh sách locale phân cách bằng dấu phẩy"><VtInput v-model="form.locales" placeholder="vi-VN, en-US" /></VtField>
          <div class="provider-switch span-two"><VtSwitch v-model="form.enabled" label="Bật provider" description="Cho phép provider tham gia routing chain đã publish." /></div>
        </div>
      </section>

      <section v-if="isGroq || isTts" class="provider-form-section">
        <header><span>03</span><div><h3>Tham số provider</h3><p>Giá trị được validate và đóng băng vào agent snapshot khi publish.</p></div></header>
        <div v-if="isGroq" class="form-grid two">
          <VtField label="Temperature" hint="0–2"><VtInput v-model="form.temperature" type="number" min="0" max="2" step="0.05" /></VtField>
          <VtField label="Top P" hint="0–1"><VtInput v-model="form.topP" type="number" min="0" max="1" step="0.05" /></VtField>
          <VtField label="Max completion tokens" hint="64–16.384"><VtInput v-model="form.maxCompletionTokens" type="number" min="64" max="16384" /></VtField>
          <VtField label="Service tier"><VtSelect v-model="form.serviceTier"><option value="on_demand">On demand</option><option value="auto">Auto</option><option value="flex">Flex</option><option value="performance">Performance</option></VtSelect></VtField>
          <VtField label="Reasoning effort"><VtSelect v-model="form.reasoningEffort"><option value="none">None</option><option value="default">Default</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></VtSelect></VtField>
          <div class="provider-switch span-two"><VtSwitch v-model="form.streamProseResponse" label="Stream câu trả lời sang TTS" description="Planner chỉ quyết định luồng; nội dung trả lời được stream theo từng câu để TTS bắt đầu sớm." /></div>
        </div>
        <div v-else class="form-grid two">
          <VtField label="Voice mặc định" hint="Preset giọng của VieNeu local"><VtInput v-model="form.voice" placeholder="Trúc Ly" /></VtField>
          <VtField label="Tốc độ" hint="1,0 cho chất lượng và stream ổn định nhất"><VtInput v-model="form.rate" type="number" min="0.5" max="2" step="0.05" /></VtField>
          <VtField v-if="supportsPitch" label="Cao độ Hz"><VtInput v-model="form.pitchHz" type="number" min="-100" max="100" /></VtField>
          <VtField label="Âm lượng" hint="0–1,5"><VtInput v-model="form.volume" type="number" min="0" max="1.5" step="0.05" /></VtField>
          <VtField label="Sample rate" hint="8.000–48.000 Hz"><VtInput v-model="form.outputSampleRate" type="number" min="8000" max="48000" step="1000" /></VtField>
        </div>
      </section>

      <section class="provider-form-section">
        <header><span>04</span><div><h3>Credential</h3><p>Manager chỉ cho phép giữ, thay hoặc xóa secret; giá trị hiện tại không bao giờ được đọc ngược.</p></div></header>
        <div class="form-grid two">
          <VtField label="Xử lý secret"><VtSelect v-model="form.secretAction" name="secretAction"><option value="keep">Giữ nguyên</option><option value="rotate">Thay secret</option><option value="clear">Xóa secret</option></VtSelect></VtField>
          <VtField v-if="form.secretAction === 'rotate'" label="Secret mới" :error="error" required><VtInput v-model="form.secret" type="password" autocomplete="new-password" /></VtField>
          <div v-else class="provider-secret-note"><VtIcon name="check" :size="17" /><span><b>{{ form.secretAction === "clear" ? "Secret sẽ được xóa" : "Secret được giữ nguyên" }}</b><small>Không có credential nào được gửi về trình duyệt.</small></span></div>
        </div>
      </section>
      <p v-if="error && form.secretAction !== 'rotate'" class="inline-error" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <div class="dialog-action-layout">
        <span><VtIcon name="warning" :size="16" /> Lưu cấu hình sẽ đưa health về trạng thái chưa đo cho đến lần Test tiếp theo.</span>
        <div><VtButton variant="quiet" @click="emit('close')">Hủy</VtButton><VtButton form="provider-config-form" type="submit" :busy="busy">Lưu provider</VtButton></div>
      </div>
    </template>
  </VtDialog>
</template>
