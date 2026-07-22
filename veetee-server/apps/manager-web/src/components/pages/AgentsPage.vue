<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import type { Agent, Provider } from "../../api/schemas";
import type { AgentDraftInput } from "../../types/manager";
import { VtBadge, VtButton, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect, VtTextarea } from "../ui";

const props = defineProps<{
  agents: Agent[];
  providers: Provider[];
  publishAgent: (input: AgentDraftInput) => Promise<void>;
}>();

const selectedId = ref("");
const busy = ref(false);
const error = ref("");
const form = reactive({
  name: "", locale: "vi-VN", mode: "auto" as Agent["interactionMode"], persona: "",
  firstInput: 15, betweenTurns: 30, closingGrace: 3, maxSession: 600,
  vad: "", asr: "", llm: "", tts: "",
});

const selected = computed(() => props.agents.find((agent) => agent.id === selectedId.value) ?? props.agents[0]);
const enabledProviders = (kind: Provider["kind"]) => props.providers.filter((provider) => provider.kind === kind && provider.enabled);

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function chainProvider(agent: Agent, kind: string): string {
  const chains = Array.isArray(agent.draftConfig.providerChains) ? agent.draftConfig.providerChains : [];
  const chain = chains.find((item) => {
    const value = objectValue(item);
    return value.kind === kind && value.locale === agent.defaultLocale;
  });
  const ids = Array.isArray(objectValue(chain).providerIds) ? objectValue(chain).providerIds as unknown[] : [];
  return typeof ids[0] === "string" ? ids[0] : enabledProviders(kind as Provider["kind"])[0]?.id ?? "";
}

watch(
  selected,
  (agent) => {
    if (!agent) return;
    selectedId.value = agent.id;
    const conversation = objectValue(agent.draftConfig.conversation);
    form.name = agent.name;
    form.locale = agent.defaultLocale;
    form.mode = agent.interactionMode;
    form.persona = agent.persona;
    form.firstInput = Number(conversation.firstInputSeconds ?? 15);
    form.betweenTurns = Number(conversation.betweenTurnsSeconds ?? 30);
    form.closingGrace = Number(conversation.closingGraceSeconds ?? 3);
    form.maxSession = Number(conversation.maxSessionSeconds ?? 600);
    form.vad = chainProvider(agent, "vad");
    form.asr = chainProvider(agent, "asr");
    form.llm = chainProvider(agent, "llm");
    form.tts = chainProvider(agent, "tts");
    error.value = "";
  },
  { immediate: true },
);

async function publish(): Promise<void> {
  if (!selected.value) return;
  busy.value = true;
  error.value = "";
  try {
    const providerChains = (["vad", "asr", "llm", "tts"] as const).map((kind) => ({
      kind,
      locale: form.locale,
      providerIds: form[kind] ? [form[kind]] : [],
    }));
    await props.publishAgent({
      id: selected.value.id,
      name: form.name.trim(),
      defaultLocale: form.locale,
      interactionMode: form.mode,
      persona: form.persona.trim(),
      draftConfig: {
        providerChains,
        conversation: {
          firstInputSeconds: Number(form.firstInput),
          betweenTurnsSeconds: Number(form.betweenTurns),
          closingGraceSeconds: Number(form.closingGrace),
          maxSessionSeconds: Number(form.maxSession),
        },
      },
    });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : "Không thể publish agent.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="vt-page" data-page="agents">
    <VtPageHeader eyebrow="ASSISTANTS / AGENT CONFIG" title="Tính cách và luồng hội thoại" description="Mỗi lần publish tạo một version bất biến. Draft mới không ảnh hưởng robot cho đến khi được publish và rollout." />

    <div v-if="agents.length" class="agent-layout">
      <aside class="agent-list">
        <button v-for="agent in agents" :key="agent.id" type="button" :class="{ active: selected?.id === agent.id }" @click="selectedId = agent.id">
          <span class="agent-list-avatar">{{ agent.name.slice(0, 1).toUpperCase() }}</span><span><b>{{ agent.name }}</b><small>{{ agent.defaultLocale }} · {{ agent.interactionMode }}</small></span><VtBadge :tone="agent.version === agent.publishedVersion ? 'success' : 'warning'">v{{ agent.publishedVersion }}</VtBadge>
        </button>
      </aside>

      <form v-if="selected" class="agent-editor" @submit.prevent="publish">
        <article class="vt-panel agent-editor-hero">
          <div class="agent-editor-avatar">{{ form.name.slice(0, 1).toUpperCase() || "V" }}</div>
          <div><span class="vt-kicker">AGENT {{ selected.id }}</span><h2>{{ form.name }}</h2><p>Draft v{{ selected.version }} · Published v{{ selected.publishedVersion }}</p></div>
          <VtBadge :tone="selected.version === selected.publishedVersion ? 'success' : 'warning'" dot>{{ selected.version === selected.publishedVersion ? "Đang đồng bộ" : "Có thay đổi draft" }}</VtBadge>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">01</span><h2>Danh tính</h2><p>Tên hiển thị, ngôn ngữ và persona được gửi vào runtime.</p></div></header>
          <div class="form-grid two">
            <VtField label="Tên trợ lý" required><VtInput v-model="form.name" maxlength="80" required /></VtField>
            <VtField label="Ngôn ngữ mặc định" hint="BCP 47 locale"><VtSelect v-model="form.locale"><option value="vi-VN">Tiếng Việt · vi-VN</option><option value="en-US">English · en-US</option></VtSelect></VtField>
            <VtField label="Chế độ tương tác" hint="Tự động là trải nghiệm mặc định; nút và wake word cùng mở hoặc ngắt một phiên."><VtSelect v-model="form.mode"><option value="auto">Tự động · nói là xử lý trong phiên</option><option value="realtime">Realtime thử nghiệm · yêu cầu AEC/barge-in</option><option value="manual">Thủ công / PTT · chế độ tương thích</option></VtSelect></VtField>
            <div v-if="form.mode === 'realtime'" class="agent-mode-note span-two"><VtIcon name="warning" :size="18" /><p><b>Realtime đang ở mức thử nghiệm.</b><span>Chỉ dùng khi provider realtime, AEC và barge-in đã vượt benchmark; chế độ này không thay đổi logic nói → AI nghe → xử lý → trả lời.</span></p></div>
            <VtField label="Tính cách / persona" class="span-two" required><VtTextarea v-model="form.persona" rows="5" required /></VtField>
          </div>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">02</span><h2>Provider chain</h2><p>Routing theo capability và locale; provider khác ngôn ngữ vẫn được giữ nguyên.</p></div></header>
          <div class="form-grid two">
            <VtField label="VAD"><VtSelect v-model="form.vad"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('vad')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="ASR"><VtSelect v-model="form.asr"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('asr')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="LLM"><VtSelect v-model="form.llm"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('llm')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="TTS"><VtSelect v-model="form.tts"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('tts')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
          </div>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">03</span><h2>Timeout hội thoại</h2><p>Giới hạn rõ ràng giúp robot không treo ở trạng thái nghe mãi.</p></div></header>
          <div class="form-grid four">
            <VtField label="Chờ câu đầu" hint="3–300 giây"><VtInput v-model="form.firstInput" type="number" min="3" max="300" /></VtField>
            <VtField label="Giữa các lượt" hint="3–600 giây"><VtInput v-model="form.betweenTurns" type="number" min="3" max="600" /></VtField>
            <VtField label="Thời gian chào kết thúc" hint="0,5–60 giây"><VtInput v-model="form.closingGrace" type="number" min="0.5" max="60" step="0.5" /></VtField>
            <VtField label="Giới hạn phiên" hint="10–3.600 giây"><VtInput v-model="form.maxSession" type="number" min="10" max="3600" /></VtField>
          </div>
        </article>

        <div class="sticky-publish"><div><b>Publish tạo version mới</b><small>Robot chỉ nhận sau rollout; extension fields không thuộc form này vẫn được giữ.</small></div><p v-if="error" class="inline-error">{{ error }}</p><VtButton type="submit" :busy="busy"><VtIcon name="upload" :size="17" /> Publish version {{ selected.version + 1 }}</VtButton></div>
      </form>
    </div>
    <VtEmptyState v-else icon="agent" title="Chưa có agent" text="Manager API chưa trả về agent nào cho workspace này." />
  </section>
</template>
