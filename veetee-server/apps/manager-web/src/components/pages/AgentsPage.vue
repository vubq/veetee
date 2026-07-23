<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";

import type { Agent, AgentPromptCatalog, Provider } from "../../api/schemas";
import type { AgentDraftInput } from "../../types/manager";
import { VtBadge, VtButton, VtDialog, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect, VtTextarea } from "../ui";

const props = defineProps<{
  agents: Agent[];
  providers: Provider[];
  promptCatalog: AgentPromptCatalog | undefined;
  publishAgent: (input: AgentDraftInput) => Promise<void>;
  createAgent: (input: { name: string; defaultLocale: string; interactionMode: Agent["interactionMode"]; persona: string; draftConfig?: Record<string, unknown> }) => Promise<Agent>;
}>();

interface PromptDraft {
  schemaVersion: 1;
  template: string;
  language: string;
  timeZone: string;
  timeZoneSource: "device" | "fixed";
  personalityPresetId: string;
  customPersonality: string;
  responseStyle: string;
  userAddress: string;
}

const selectedId = ref("");
const busy = ref(false);
const error = ref("");
const createOpen = ref(false);
const createBusy = ref(false);
const createError = ref("");
const createForm = reactive({
  name: "",
  locale: "vi-VN",
  language: "Tiếng Việt",
  mode: "auto" as Agent["interactionMode"],
  persona: "",
  personalityPresetId: "warm-empathetic",
});
const form = reactive({
  name: "", locale: "vi-VN", mode: "auto" as Agent["interactionMode"], persona: "",
  language: "Tiếng Việt", timeZone: browserTimeZone(), timeZoneSource: "device" as "device" | "fixed",
  personalityPresetId: "warm-empathetic", customPersonality: "",
  responseStyle: "Tự nhiên, rõ ràng và vừa đủ chi tiết cho một cuộc trò chuyện bằng giọng nói.",
  userAddress: "", promptTemplate: "",
  firstInput: 15, betweenTurns: 30, closingGrace: 5, maxSession: 600,
  vad: "", asr: "", llm: "", tts: "",
});

const selected = computed(() => props.agents.find((agent) => agent.id === selectedId.value) ?? props.agents[0]);
const personalityPresets = computed(() => props.promptCatalog?.personalityPresets ?? []);
const selectedPersonality = computed(() => personalityPresets.value.find((preset) => preset.id === form.personalityPresetId));
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

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function defaultPersonalityId(): string {
  return personalityPresets.value.find((preset) => preset.id === "warm-empathetic")?.id
    ?? personalityPresets.value[0]?.id
    ?? "warm-empathetic";
}

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Bangkok";
  } catch {
    return "Asia/Bangkok";
  }
}

function promptDraft(value: unknown, locale: string): PromptDraft {
  const prompt = objectValue(value);
  const presetId = stringValue(prompt.personalityPresetId, defaultPersonalityId());
  return {
    schemaVersion: 1,
    template: stringValue(prompt.template, props.promptCatalog?.defaultTemplate ?? ""),
    language: stringValue(prompt.language, locale),
    timeZone: stringValue(prompt.timeZone, browserTimeZone()),
    timeZoneSource: prompt.timeZoneSource === "fixed" ? "fixed" : "device",
    personalityPresetId: personalityPresets.value.some((preset) => preset.id === presetId)
      ? presetId
      : defaultPersonalityId(),
    customPersonality: stringValue(prompt.customPersonality),
    responseStyle: stringValue(
      prompt.responseStyle,
      "Tự nhiên, rõ ràng và vừa đủ chi tiết cho một cuộc trò chuyện bằng giọng nói.",
    ),
    userAddress: stringValue(prompt.userAddress),
  };
}

watch(
  [selected, () => props.promptCatalog],
  ([agent]) => {
    if (!agent) return;
    selectedId.value = agent.id;
    const conversation = objectValue(agent.draftConfig.conversation);
    const prompt = promptDraft(agent.draftConfig.prompt, agent.defaultLocale);
    form.name = agent.name;
    form.locale = agent.defaultLocale;
    form.mode = agent.interactionMode;
    form.persona = agent.persona;
    form.language = prompt.language;
    form.timeZone = prompt.timeZone;
    form.timeZoneSource = prompt.timeZoneSource;
    form.personalityPresetId = prompt.personalityPresetId;
    form.customPersonality = prompt.customPersonality;
    form.responseStyle = prompt.responseStyle;
    form.userAddress = prompt.userAddress;
    form.promptTemplate = prompt.template;
    form.firstInput = Number(conversation.firstInputSeconds ?? 15);
    form.betweenTurns = Number(conversation.betweenTurnsSeconds ?? 30);
    form.closingGrace = Number(conversation.closingGraceSeconds ?? 5);
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
        prompt: {
          schemaVersion: 1,
          template: form.promptTemplate,
          language: form.language.trim(),
          timeZone: form.timeZone.trim(),
          timeZoneSource: form.timeZoneSource,
          personalityPresetId: form.personalityPresetId,
          customPersonality: form.customPersonality.trim(),
          responseStyle: form.responseStyle.trim(),
          userAddress: form.userAddress.trim(),
        },
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

async function create(): Promise<void> {
  if (!createForm.name.trim() || !createForm.persona.trim() || !createForm.language.trim()) {
    createError.value = "Tên, ngôn ngữ và vai trò riêng là bắt buộc.";
    return;
  }
  if (!props.promptCatalog) {
    createError.value = "Catalog prompt chưa tải xong.";
    return;
  }
  createBusy.value = true;
  createError.value = "";
  try {
    const agent = await props.createAgent({
      name: createForm.name.trim(),
      defaultLocale: createForm.locale,
      interactionMode: createForm.mode,
      persona: createForm.persona.trim(),
      draftConfig: {
        prompt: {
          schemaVersion: 1,
          template: props.promptCatalog.defaultTemplate,
          language: createForm.language.trim(),
          timeZone: browserTimeZone(),
          timeZoneSource: "device",
          personalityPresetId: createForm.personalityPresetId,
          customPersonality: "",
          responseStyle: "Tự nhiên, rõ ràng và vừa đủ chi tiết cho một cuộc trò chuyện bằng giọng nói.",
          userAddress: "",
        },
      },
    });
    selectedId.value = agent.id;
    createOpen.value = false;
    createForm.name = "";
    createForm.persona = "";
    createForm.personalityPresetId = defaultPersonalityId();
  } catch (exception) {
    createError.value = exception instanceof Error ? exception.message : "Không thể tạo trợ lý.";
  } finally {
    createBusy.value = false;
  }
}

function addVariable(name: string): void {
  const token = `{{${name}}}`;
  const separator = form.promptTemplate && !form.promptTemplate.endsWith("\n") ? "\n" : "";
  form.promptTemplate += `${separator}${token}`;
}

function resetPromptTemplate(): void {
  if (props.promptCatalog) form.promptTemplate = props.promptCatalog.defaultTemplate;
}

const promptPreview = computed(() => {
  const now = new Date();
  const previewTimeZone = form.timeZoneSource === "device" ? browserTimeZone() : form.timeZone;
  let currentDate = "[múi giờ chưa hợp lệ]";
  let currentTime = "[múi giờ chưa hợp lệ]";
  try {
    currentDate = new Intl.DateTimeFormat("en-CA", {
      timeZone: previewTimeZone || "UTC",
    }).format(now);
    currentTime = new Intl.DateTimeFormat("vi-VN", {
      timeZone: previewTimeZone || "UTC",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(now);
  } catch {
    // Server validates the IANA time zone before publishing.
  }
  const personality = [
    selectedPersonality.value?.instructions ?? "",
    form.customPersonality.trim(),
  ].filter(Boolean).join("\n");
  const values: Record<string, string> = {
    agent_name: form.name || "VeeTee",
    language: form.language,
    locale: form.locale,
    persona: form.persona,
    personality,
    response_style: form.responseStyle,
    user_address: form.userAddress,
    interaction_mode: form.mode,
    config_version: String((selected.value?.version ?? 0) + 1),
    current_date: currentDate,
    current_time: currentTime,
    timezone: previewTimeZone,
    device_locale: form.locale,
    device_timezone: previewTimeZone,
    device_timezone_offset: "UTC offset theo thiết bị",
    available_tools: "[runtime tool catalog]",
  };
  return form.promptTemplate.replace(
    /{{\s*([a-z_][a-z0-9_]*)\s*}}/g,
    (token, name: string) => values[name] ?? token,
  );
});
</script>

<template>
  <section class="vt-page" data-page="agents">
    <VtPageHeader eyebrow="ASSISTANTS / AGENT CONFIG" title="Tính cách và luồng hội thoại" description="Mỗi lần publish tạo một version bất biến. Draft mới không ảnh hưởng robot cho đến khi được publish và rollout.">
      <template #actions><VtButton @click="createOpen = true"><VtIcon name="plus" :size="16" /> Tạo trợ lý</VtButton></template>
    </VtPageHeader>

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
          <header class="panel-header"><div><span class="section-index">01</span><h2>Danh tính và ngôn ngữ</h2><p>Tên, locale kỹ thuật và tên ngôn ngữ được đóng băng trong version publish.</p></div></header>
          <div class="form-grid two">
            <VtField label="Tên trợ lý" hint="Tên hiển thị trên Manager và màn hình robot" required><VtInput v-model="form.name" maxlength="80" required /></VtField>
            <VtField label="Ngôn ngữ mặc định" hint="Locale agent/provider là fallback BCP-47; thiết bị sẽ báo locale thực tế sau provisioning." required><VtInput v-model="form.locale" maxlength="35" placeholder="vi-VN" required /></VtField>
            <VtField label="Ngôn ngữ AI trả lời" hint="Giá trị tự do cho biến {{language}}, ví dụ Tiếng Việt tự nhiên" required><VtInput v-model="form.language" maxlength="120" placeholder="Tiếng Việt tự nhiên" required /></VtField>
            <VtField label="Nguồn múi giờ" hint="Ưu tiên timezone thiết bị đã báo; fallback dùng khi chưa có report." required><VtSelect v-model="form.timeZoneSource"><option value="device">Thiết bị · khuyến nghị</option><option value="fixed">Cố định theo agent</option></VtSelect></VtField>
            <VtField v-if="form.timeZoneSource === 'fixed'" label="Múi giờ fallback" hint="IANA cho {{current_date}} và {{current_time}}" required><VtInput v-model="form.timeZone" maxlength="80" placeholder="Asia/Bangkok" required /></VtField>
            <div v-else class="agent-mode-note span-two"><VtIcon name="check" :size="18" /><p><b>Timezone lấy từ thiết bị khi có thể.</b><span>Wi‑Fi chỉ cung cấp đường truyền; firmware gửi locale và múi giờ đã lưu trong reported state. Live preview dùng múi giờ của trình duyệt.</span></p></div>
            <VtField label="Chế độ tương tác" class="span-two" hint="Tự động là trải nghiệm mặc định; nút và wake word cùng mở hoặc ngắt một phiên."><VtSelect v-model="form.mode"><option value="auto">Tự động · nói là xử lý trong phiên</option><option value="realtime">Realtime thử nghiệm · yêu cầu AEC/barge-in</option><option value="manual">Thủ công / PTT · chế độ tương thích</option></VtSelect></VtField>
            <div v-if="form.mode === 'realtime'" class="agent-mode-note span-two"><VtIcon name="warning" :size="18" /><p><b>Realtime đang ở mức thử nghiệm.</b><span>Chỉ dùng khi provider realtime, AEC và barge-in đã vượt benchmark; chế độ này không thay đổi logic nói → AI nghe → xử lý → trả lời.</span></p></div>
          </div>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">02</span><h2>Tính cách</h2><p>Preset là dữ liệu prompt, không tạo nhánh logic trong runtime. An toàn, quyền tool và sự thật luôn được giữ riêng.</p></div></header>
          <div class="personality-grid" role="radiogroup" aria-label="Chọn tính cách">
            <button
              v-for="preset in personalityPresets"
              :key="preset.id"
              type="button"
              role="radio"
              :aria-checked="form.personalityPresetId === preset.id"
              :class="['personality-card', `accent-${preset.accent}`, { active: form.personalityPresetId === preset.id }]"
              @click="form.personalityPresetId = preset.id"
            >
              <span class="personality-mark" aria-hidden="true">{{ preset.label.slice(0, 1) }}</span>
              <span class="personality-copy"><b>{{ preset.label }}</b><small>{{ preset.summary }}</small></span>
              <i v-if="form.personalityPresetId === preset.id" class="personality-selected" aria-hidden="true"><VtIcon name="check" :size="13" /></i>
            </button>
          </div>
          <div class="form-grid two personality-details">
            <VtField label="Tính cách / persona" hint="Nội dung cho {{persona}}: chuyên môn, mục tiêu và giới hạn của trợ lý." required><VtTextarea v-model="form.persona" rows="5" required /></VtField>
            <VtField label="Tinh chỉnh tính cách" hint="Bổ sung cho preset, không thay thế safety/tool policy."><VtTextarea v-model="form.customPersonality" rows="5" maxlength="4000" placeholder="Ví dụ: thích bắt bẻ vui khi người dùng đang trêu đùa." /></VtField>
            <VtField label="Phong cách trả lời" hint="Nội dung cho {{response_style}}"><VtTextarea v-model="form.responseStyle" rows="3" maxlength="2000" /></VtField>
            <VtField label="Cách xưng hô" hint="Nội dung cho {{user_address}}; có thể để trống."><VtInput v-model="form.userAddress" maxlength="120" placeholder="bạn, anh Khoa, chị…" /></VtField>
          </div>
          <div v-if="selectedPersonality" class="personality-preview">
            <span>PRESET ĐƯỢC ĐÓNG BĂNG KHI PUBLISH</span>
            <p>{{ selectedPersonality.instructions }}</p>
          </div>
        </article>

        <article class="vt-panel form-section prompt-section">
          <header class="panel-header prompt-section-header">
            <div><span class="section-index">03</span><h2>Agent base prompt</h2><p>Template raw tương tự `agent-base-prompt.txt`, chỉ hỗ trợ token allowlist và không chạy biểu thức.</p></div>
            <VtButton type="button" variant="quiet" size="sm" @click="resetPromptTemplate"><VtIcon name="refresh" :size="15" /> Khôi phục mặc định</VtButton>
          </header>
          <div class="prompt-token-bar">
            <div class="prompt-token-heading">
              <b>Chèn biến vào template</b>
              <small>Chọn một token để thêm vào vị trí cuối con trỏ.</small>
            </div>
            <div class="prompt-variables" aria-label="Biến template">
              <button v-for="variable in promptCatalog?.variables ?? []" :key="variable.name" type="button" :title="variable.description" @click="addVariable(variable.name)">
                <code v-text="`{{${variable.name}}}`"></code>
                <span>{{ variable.required ? "bắt buộc" : variable.dynamic ? "runtime" : "tùy chọn" }}</span>
              </button>
            </div>
          </div>
          <div class="prompt-editor-grid">
            <VtField label="Template bản nháp" hint="Bắt buộc có {{agent_name}}, {{language}}, {{persona}} và {{personality}}." required><VtTextarea v-model="form.promptTemplate" class="prompt-template-input" rows="22" maxlength="20000" spellcheck="false" required /></VtField>
            <div class="prompt-render-preview">
              <header><span>LIVE PREVIEW</span><small>Ví dụ render với dữ liệu trợ lý hiện tại</small></header>
              <pre>{{ promptPreview }}</pre>
            </div>
          </div>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">04</span><h2>Provider chain</h2><p>Routing theo capability và locale; provider khác ngôn ngữ vẫn được giữ nguyên.</p></div></header>
          <div class="form-grid two">
            <VtField label="VAD"><VtSelect v-model="form.vad"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('vad')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="ASR"><VtSelect v-model="form.asr"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('asr')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="LLM"><VtSelect v-model="form.llm"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('llm')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
            <VtField label="TTS"><VtSelect v-model="form.tts"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('tts')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
          </div>
        </article>

        <article class="vt-panel form-section">
          <header class="panel-header"><div><span class="section-index">05</span><h2>Timeout hội thoại</h2><p>Giới hạn rõ ràng giúp robot không treo ở trạng thái nghe mãi.</p></div></header>
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

    <VtDialog :open="createOpen" title="Tạo trợ lý mới" eyebrow="ASSISTANTS / NEW PROFILE" icon="agent" description="Tạo draft độc lập. Trợ lý chỉ dùng được cho Lab hoặc thiết bị sau khi đã publish config." width="sm" @close="createOpen = false">
      <form id="create-agent-form" class="form-stack" @submit.prevent="create">
        <VtField label="Tên trợ lý" required><VtInput v-model="createForm.name" maxlength="80" placeholder="Ví dụ: Cô giáo Khoa học" required /></VtField>
        <div class="form-grid two">
          <VtField label="Locale"><VtInput v-model="createForm.locale" maxlength="35" placeholder="vi-VN" /></VtField>
          <VtField label="Ngôn ngữ AI"><VtInput v-model="createForm.language" maxlength="120" placeholder="Tiếng Việt tự nhiên" /></VtField>
          <VtField label="Chế độ"><VtSelect v-model="createForm.mode"><option value="auto">Tự động</option><option value="manual">PTT tương thích</option><option value="realtime">Realtime thử nghiệm</option></VtSelect></VtField>
          <VtField label="Tính cách"><VtSelect v-model="createForm.personalityPresetId"><option v-for="preset in personalityPresets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></VtSelect></VtField>
        </div>
        <VtField label="Tính cách / persona" hint="Tính cách chọn từ preset; ô này mô tả chuyên môn, mục tiêu và giới hạn riêng." required><VtTextarea v-model="createForm.persona" rows="5" placeholder="Trợ lý giải thích khoa học cho trẻ em, ưu tiên ví dụ gần gũi và chính xác." required /></VtField>
        <p v-if="createError" class="inline-error" role="alert">{{ createError }}</p>
      </form>
      <template #footer><VtButton variant="quiet" @click="createOpen = false">Hủy</VtButton><VtButton form="create-agent-form" type="submit" :busy="createBusy"><VtIcon name="plus" :size="16" /> Tạo draft</VtButton></template>
    </VtDialog>
  </section>
</template>
