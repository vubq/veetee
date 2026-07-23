<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";

import type { Agent, AgentPromptCatalog, PersonalityPreset, Provider } from "../../api/schemas";
import type { AgentDraftInput } from "../../types/manager";
import { VtBadge, VtButton, VtDialog, VtEmptyState, VtField, VtIcon, VtInput, VtPageHeader, VtSelect, VtTextarea } from "../ui";

const props = defineProps<{
  agents: Agent[];
  providers: Provider[];
  promptCatalog: AgentPromptCatalog | undefined;
  publishAgent: (input: AgentDraftInput) => Promise<void>;
  createAgent: (input: { name: string; defaultLocale: string; interactionMode: Agent["interactionMode"]; persona: string; draftConfig?: Record<string, unknown> }) => Promise<Agent>;
  createPersonalityPreset: (input: { label: string; summary: string; accent: string; instructions: string }) => Promise<PersonalityPreset>;
  deletePersonalityPreset: (id: string) => Promise<void>;
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
const personalityOpen = ref(false);
const personalityBusy = ref(false);
const personalityError = ref("");
const deletePersonalityOpen = ref(false);
const deletePersonalityBusy = ref(false);
const deletePersonalityError = ref("");
const personalityToDelete = ref<PersonalityPreset | null>(null);
const localPersonalityPresets = ref<PersonalityPreset[]>([]);
const deletedPersonalityIds = ref(new Set<string>());
const createForm = reactive({
  name: "",
  locale: "vi-VN",
  language: "Tiếng Việt",
  mode: "auto" as Agent["interactionMode"],
  persona: "",
  personalityPresetId: "warm-empathetic",
});
const personalityForm = reactive({
  label: "",
  summary: "",
  accent: "coral",
  instructions: "",
});
const personalityAccents = [
  { id: "coral", label: "San hô", color: "#e06b51" },
  { id: "sun", label: "Nắng", color: "#c99324" },
  { id: "cyan", label: "Biển", color: "#287f8e" },
  { id: "lime", label: "Mầm xanh", color: "#7e9b2f" },
  { id: "violet", label: "Tím khói", color: "#735b91" },
  { id: "navy", label: "Xanh đậm", color: "#173e49" },
  { id: "pink", label: "Hồng đất", color: "#c25973" },
] as const;
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
const personalityPresets = computed(() => {
  const serverPresets = (props.promptCatalog?.personalityPresets ?? []).filter(
    (preset) => !deletedPersonalityIds.value.has(preset.id),
  );
  const serverIds = new Set(serverPresets.map((preset) => preset.id));
  return [
    ...serverPresets,
    ...localPersonalityPresets.value.filter(
      (preset) => !serverIds.has(preset.id) && !deletedPersonalityIds.value.has(preset.id),
    ),
  ];
});
const selectedPersonality = computed(() => personalityPresets.value.find((preset) => preset.id === form.personalityPresetId));
const selectedPersonalityAccent = computed(
  () => personalityAccents.find((accent) => accent.id === personalityForm.accent) ?? personalityAccents[0],
);
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

function defaultPersonalityId(excludeId = ""): string {
  return personalityPresets.value.find((preset) => preset.id === "warm-empathetic" && preset.id !== excludeId)?.id
    ?? personalityPresets.value.find((preset) => preset.id !== excludeId)?.id
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

function openPersonalityCreate(): void {
  personalityForm.label = "";
  personalityForm.summary = "";
  personalityForm.accent = "coral";
  personalityForm.instructions = "";
  personalityError.value = "";
  personalityOpen.value = true;
}

async function createPersonality(): Promise<void> {
  if (
    !personalityForm.label.trim() ||
    !personalityForm.summary.trim() ||
    !personalityForm.instructions.trim()
  ) {
    personalityError.value = "Tên, mô tả ngắn và hướng dẫn tính cách là bắt buộc.";
    return;
  }
  personalityBusy.value = true;
  personalityError.value = "";
  try {
    const preset = await props.createPersonalityPreset({
      label: personalityForm.label.trim(),
      summary: personalityForm.summary.trim(),
      accent: personalityForm.accent,
      instructions: personalityForm.instructions.trim(),
    });
    localPersonalityPresets.value = [...localPersonalityPresets.value, preset];
    deletedPersonalityIds.value.delete(preset.id);
    personalityOpen.value = false;
    await nextTick();
    form.personalityPresetId = preset.id;
    createForm.personalityPresetId = preset.id;
  } catch (exception) {
    personalityError.value =
      exception instanceof Error ? exception.message : "Không thể tạo tính cách.";
  } finally {
    personalityBusy.value = false;
  }
}

function askDeletePersonality(preset: PersonalityPreset): void {
  if (!preset.deletable || preset.builtIn) return;
  personalityToDelete.value = preset;
  deletePersonalityError.value = "";
  deletePersonalityOpen.value = true;
}

async function deletePersonality(): Promise<void> {
  const preset = personalityToDelete.value;
  if (!preset) return;
  deletePersonalityBusy.value = true;
  deletePersonalityError.value = "";
  try {
    await props.deletePersonalityPreset(preset.id);
    deletedPersonalityIds.value.add(preset.id);
    localPersonalityPresets.value = localPersonalityPresets.value.filter(
      (candidate) => candidate.id !== preset.id,
    );
    await nextTick();
    if (form.personalityPresetId === preset.id) {
      form.personalityPresetId = defaultPersonalityId(preset.id);
    }
    if (createForm.personalityPresetId === preset.id) {
      createForm.personalityPresetId = defaultPersonalityId(preset.id);
    }
    deletePersonalityOpen.value = false;
    personalityToDelete.value = null;
  } catch (exception) {
    deletePersonalityError.value =
      exception instanceof Error
        ? exception.message
        : "Không thể xóa tính cách này.";
  } finally {
    deletePersonalityBusy.value = false;
  }
}

function addVariable(name: string): void {
  const token = `{{${name}}}`;
  const separator = form.promptTemplate && !form.promptTemplate.endsWith("\n") ? "\n" : "";
  form.promptTemplate += `${separator}${token}`;
}

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
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

        <nav class="agent-config-nav" aria-label="Các phần cấu hình trợ lý">
          <a href="#agent-identity" @click.prevent="scrollToSection('agent-identity')"><span>01</span><div><b>Danh tính</b><small>Tên & ngôn ngữ</small></div></a>
          <a href="#agent-personality" @click.prevent="scrollToSection('agent-personality')"><span>02</span><div><b>Tính cách</b><small>Persona & giọng điệu</small></div></a>
          <a href="#agent-prompt" @click.prevent="scrollToSection('agent-prompt')"><span>03</span><div><b>Base prompt</b><small>Template & preview</small></div></a>
          <a href="#agent-runtime" @click.prevent="scrollToSection('agent-runtime')"><span>04</span><div><b>Runtime</b><small>Provider & timeout</small></div></a>
        </nav>

        <article id="agent-identity" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">01</span>
            <div><span class="vt-kicker">CORE PROFILE</span><h2>Danh tính và ngôn ngữ</h2><p>Tên, locale kỹ thuật và tên ngôn ngữ được đóng băng trong version publish.</p></div>
          </header>
          <div class="agent-section-content">
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
          </div>
        </article>

        <article id="agent-personality" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">02</span>
            <div><span class="vt-kicker">VOICE & STANCE</span><h2>Tính cách</h2><p>Preset là dữ liệu prompt, không tạo nhánh logic trong runtime. An toàn, quyền tool và sự thật luôn được giữ riêng.</p></div>
            <VtButton type="button" variant="quiet" size="sm" data-testid="create-personality" @click="openPersonalityCreate"><VtIcon name="plus" :size="15" /> Thêm tính cách</VtButton>
          </header>
          <div class="agent-section-content">
          <div v-if="selectedPersonality" class="personality-feature">
            <span class="personality-feature-mark">{{ selectedPersonality.label.slice(0, 1) }}</span>
            <div><span class="personality-feature-kicker">{{ selectedPersonality.builtIn ? "PRESET THƯ VIỆN" : "PRESET TÙY CHỈNH" }}</span><h3>{{ selectedPersonality.label }}</h3><p>{{ selectedPersonality.summary }}</p></div>
            <span class="personality-feature-state"><VtIcon name="check" :size="14" /> ĐANG CHỌN</span>
          </div>
          <div class="personality-grid" role="radiogroup" aria-label="Chọn tính cách">
            <div
              v-for="preset in personalityPresets"
              :key="preset.id"
              :class="[
                'personality-card',
                `accent-${preset.accent}`,
                {
                  active: form.personalityPresetId === preset.id,
                  deletable: preset.deletable && !preset.builtIn,
                },
              ]"
            >
              <button
                type="button"
                class="personality-choice"
                role="radio"
                :aria-checked="form.personalityPresetId === preset.id"
                @click="form.personalityPresetId = preset.id"
              >
                <span class="personality-mark" aria-hidden="true">{{ preset.label.slice(0, 1) }}</span>
                <span class="personality-copy">
                  <span class="personality-card-meta">{{ preset.builtIn ? "THƯ VIỆN" : "TÙY CHỈNH" }}</span>
                  <b>{{ preset.label }}</b>
                  <small>{{ preset.summary }}</small>
                </span>
                <i v-if="form.personalityPresetId === preset.id" class="personality-selected" aria-hidden="true"><VtIcon name="check" :size="13" /></i>
              </button>
              <button
                v-if="preset.deletable && !preset.builtIn"
                type="button"
                class="personality-delete"
                :aria-label="`Xóa tính cách ${preset.label}`"
                @click.stop="askDeletePersonality(preset)"
              >
                <VtIcon name="trash" :size="14" />
              </button>
            </div>
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
          </div>
        </article>

        <article id="agent-prompt" class="vt-panel form-section agent-config-section prompt-section">
          <header class="agent-section-header prompt-section-header">
            <span class="agent-section-index">03</span>
            <div><span class="vt-kicker">PROMPT WORKBENCH</span><h2>Agent base prompt</h2><p>Template raw tương tự `agent-base-prompt.txt`, chỉ hỗ trợ token allowlist và không chạy biểu thức.</p></div>
            <VtButton type="button" variant="quiet" size="sm" @click="resetPromptTemplate"><VtIcon name="refresh" :size="15" /> Khôi phục mặc định</VtButton>
          </header>
          <div class="agent-section-content">
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
            <section class="prompt-workbench-pane">
              <header class="prompt-pane-header">
                <div><span class="prompt-pane-kicker">SOURCE / DRAFT</span><b>Template bản nháp</b><small>Bắt buộc có &#123;&#123;agent_name&#125;&#125;, &#123;&#123;language&#125;&#125;, &#123;&#123;persona&#125;&#125; và &#123;&#123;personality&#125;&#125;.</small></div>
                <VtBadge tone="warning">Chưa publish</VtBadge>
              </header>
              <VtTextarea v-model="form.promptTemplate" class="prompt-template-input" aria-label="Template bản nháp" rows="18" maxlength="20000" spellcheck="false" required />
            </section>
            <section class="prompt-render-preview">
              <header><div><span>LIVE PREVIEW</span><small>Render với dữ liệu agent hiện tại</small></div><VtBadge tone="success">Token safe</VtBadge></header>
              <pre>{{ promptPreview }}</pre>
            </section>
          </div>
          </div>
        </article>

        <article id="agent-runtime" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">04</span>
            <div><span class="vt-kicker">RUNTIME POLICY</span><h2>Provider và nhịp hội thoại</h2><p>Routing theo capability và locale, cùng các giới hạn để phiên thoại không treo ở trạng thái nghe mãi.</p></div>
          </header>
          <div class="agent-section-content agent-runtime-grid">
            <section class="agent-runtime-card">
              <header><span class="agent-runtime-icon"><VtIcon name="provider" :size="17" /></span><div><b>Provider chain</b><small>Fallback theo locale của agent</small></div></header>
              <div class="form-grid two">
                <VtField label="VAD"><VtSelect v-model="form.vad"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('vad')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="ASR"><VtSelect v-model="form.asr"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('asr')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="LLM"><VtSelect v-model="form.llm"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('llm')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="TTS"><VtSelect v-model="form.tts"><option value="">Chưa chọn</option><option v-for="provider in enabledProviders('tts')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
              </div>
            </section>
            <section class="agent-runtime-card">
              <header><span class="agent-runtime-icon"><VtIcon name="telemetry" :size="17" /></span><div><b>Timeout hội thoại</b><small>Giới hạn rõ ràng cho từng phiên</small></div></header>
              <div class="form-grid two">
                <VtField label="Chờ câu đầu" hint="3–300 giây"><VtInput v-model="form.firstInput" type="number" min="3" max="300" /></VtField>
                <VtField label="Giữa các lượt" hint="3–600 giây"><VtInput v-model="form.betweenTurns" type="number" min="3" max="600" /></VtField>
                <VtField label="Chào kết thúc" hint="0,5–60 giây"><VtInput v-model="form.closingGrace" type="number" min="0.5" max="60" step="0.5" /></VtField>
                <VtField label="Giới hạn phiên" hint="10–3.600 giây"><VtInput v-model="form.maxSession" type="number" min="10" max="3600" /></VtField>
              </div>
            </section>
          </div>
        </article>

        <div class="sticky-publish"><span class="publish-mark"><VtIcon name="upload" :size="18" /></span><div><b>Publish tạo version mới</b><small>Robot chỉ nhận sau rollout; extension fields không thuộc form này vẫn được giữ.</small></div><p v-if="error" class="inline-error">{{ error }}</p><span class="publish-target"><small>VERSION</small><b>v{{ selected.version + 1 }}</b></span><VtButton type="submit" :busy="busy"><VtIcon name="upload" :size="17" /> Publish version {{ selected.version + 1 }}</VtButton></div>
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

    <VtDialog :open="personalityOpen" title="Thêm tính cách riêng" eyebrow="VOICE & STANCE / CUSTOM" icon="agent" description="Tạo một preset dữ liệu dùng lại cho agent, Realtime Lab và snapshot thiết bị." width="md" @close="personalityOpen = false">
      <form id="create-personality-form" class="form-stack" @submit.prevent="createPersonality">
        <VtField label="Tên tính cách" hint="Tên ngắn, dễ nhận ra trong thư viện." required><VtInput v-model="personalityForm.label" maxlength="80" placeholder="Ví dụ: Cà khịa vui" required /></VtField>
        <VtField label="Mô tả ngắn" hint="Hiển thị trên thẻ chọn tính cách." required><VtInput v-model="personalityForm.summary" maxlength="240" placeholder="Trêu nhẹ, sắc nhưng biết dừng đúng lúc." required /></VtField>
        <fieldset class="personality-accent-field">
          <legend>Màu nhận diện</legend>
          <div class="personality-accent-picker" role="radiogroup" aria-label="Màu nhận diện">
            <button
              v-for="accent in personalityAccents"
              :key="accent.id"
              type="button"
              role="radio"
              :aria-checked="personalityForm.accent === accent.id"
              :class="{ active: personalityForm.accent === accent.id }"
              :style="{ '--accent-swatch': accent.color }"
              @click="personalityForm.accent = accent.id"
            >
              <i aria-hidden="true"></i>
              <span>{{ accent.label }}</span>
              <VtIcon v-if="personalityForm.accent === accent.id" name="check" :size="12" />
            </button>
          </div>
        </fieldset>
        <VtField label="Hướng dẫn cho AI" hint="Chỉ mô tả tone và stance; safety, quyền tool và sự thật vẫn do policy riêng." required><VtTextarea v-model="personalityForm.instructions" rows="7" maxlength="4000" placeholder="Giữ giọng lém lỉnh..., phản biện lập luận thay vì công kích người dùng..." required /></VtField>
        <section
          class="personality-create-preview"
          :style="{ '--preview-accent': selectedPersonalityAccent.color }"
          aria-label="Xem trước thẻ tính cách"
        >
          <span class="personality-create-preview-mark">{{ personalityForm.label.trim().slice(0, 1) || "T" }}</span>
          <div>
            <small>PRESET TÙY CHỈNH · XEM TRƯỚC</small>
            <b>{{ personalityForm.label.trim() || "Tên tính cách" }}</b>
            <p>{{ personalityForm.summary.trim() || "Mô tả ngắn sẽ xuất hiện ở đây." }}</p>
          </div>
        </section>
        <p v-if="personalityError" class="inline-error" role="alert">{{ personalityError }}</p>
      </form>
      <template #footer><VtButton variant="quiet" @click="personalityOpen = false">Hủy</VtButton><VtButton form="create-personality-form" type="submit" :busy="personalityBusy"><VtIcon name="plus" :size="16" /> Lưu tính cách</VtButton></template>
    </VtDialog>

    <VtDialog :open="deletePersonalityOpen" title="Xóa tính cách riêng?" eyebrow="VOICE & STANCE / REMOVE" icon="warning" description="Preset mặc định luôn được bảo vệ. Snapshot đã publish không bị thay đổi." width="sm" @close="deletePersonalityOpen = false">
      <div class="delete-personality-confirm">
        <span class="delete-personality-mark"><VtIcon name="trash" :size="19" /></span>
        <div><b>{{ personalityToDelete?.label }}</b><p>Chỉ xóa được khi không còn agent draft nào đang tham chiếu preset này. Bạn có thể đổi preset và publish trước nếu API báo đang được dùng.</p></div>
      </div>
      <p v-if="deletePersonalityError" class="inline-error" role="alert">{{ deletePersonalityError }}</p>
      <template #footer><VtButton variant="quiet" @click="deletePersonalityOpen = false">Hủy</VtButton><VtButton variant="danger" :busy="deletePersonalityBusy" @click="deletePersonality"><VtIcon name="trash" :size="16" /> Xóa preset</VtButton></template>
    </VtDialog>
  </section>
</template>

<style scoped>
.agent-editor {
  gap: 16px;
}

.agent-config-nav {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--line);
  box-shadow: var(--shadow-sm);
}

.agent-config-nav a {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px 14px;
  color: var(--ink);
  background: var(--paper-strong);
  text-decoration: none;
  transition: background .18s ease, color .18s ease;
}

.agent-config-nav a:hover,
.agent-config-nav a:focus-visible {
  color: var(--navy);
  background: #fffaf3;
}

.agent-config-nav a:focus-visible {
  outline: 2px solid var(--orange);
  outline-offset: -2px;
}

.agent-config-nav a > span {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 10px;
  color: var(--orange-dark);
  background: #ffebe5;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .08em;
}

.agent-config-nav a > div {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.agent-config-nav b {
  overflow: hidden;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-config-nav small {
  overflow: hidden;
  color: var(--muted);
  font-size: 8px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-config-section {
  scroll-margin-top: 18px;
  overflow: hidden;
  padding: 0;
}

.agent-section-header {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 13px;
  border-bottom: 1px solid var(--line);
  padding: 21px 23px 18px;
  background: linear-gradient(135deg, #fffdfa, #f5f8f3);
}

.agent-section-index {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid #f4c4b4;
  border-radius: 13px;
  color: var(--orange-dark);
  background: #ffebe5;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .08em;
}

.agent-section-header > div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.agent-section-header h2 {
  margin: 0;
  font-size: 20px;
  letter-spacing: -.025em;
}

.agent-section-header p {
  max-width: 680px;
  margin: 0;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.55;
}

.agent-section-content {
  padding: 22px 23px 24px;
}

.agent-mode-note {
  border-color: #cfe2d5;
  color: var(--success);
  background: #f0f8f3;
}

.agent-mode-note span {
  color: var(--muted);
}

.personality-feature {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  border: 1px solid #d7e5d8;
  border-radius: 16px;
  padding: 13px 15px;
  background: linear-gradient(115deg, #eef8df, #f7fbf0);
}

.personality-feature-mark {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  border-radius: 14px;
  color: var(--lime);
  background: var(--navy);
  box-shadow: 0 8px 20px rgba(16, 44, 51, .13);
  font-size: 19px;
  font-weight: 800;
}

.personality-feature > div {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.personality-feature-kicker {
  color: var(--success);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: .13em;
}

.personality-feature h3 {
  margin: 0;
  font-size: 14px;
}

.personality-feature p {
  overflow: hidden;
  margin: 0;
  color: var(--muted);
  font-size: 9px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.personality-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 9px;
  margin-bottom: 16px;
}

.personality-card {
  grid-template-columns: 28px minmax(0, 1fr) 17px;
  min-height: 75px;
  align-items: start;
  gap: 9px;
  border-radius: 13px;
  padding: 11px;
  background: #fbfcf9;
  box-shadow: none;
}

.personality-card::after {
  display: none;
}

.personality-card:hover {
  border-color: var(--line-strong);
  background: #fffdfa;
  transform: translateY(-1px);
}

.personality-card.active {
  border-color: var(--orange);
  background: #fff8f5;
  box-shadow: 0 0 0 3px rgba(242, 100, 60, .1);
}

.personality-card > span.personality-mark {
  position: static;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 9px;
  color: color-mix(in srgb, var(--personality-accent) 78%, var(--navy));
  background: color-mix(in srgb, var(--personality-accent) 14%, white);
  font-size: 10px;
  font-weight: 800;
}

.personality-card > span.personality-copy {
  position: static;
  display: grid;
  width: auto;
  height: auto;
  min-width: 0;
  gap: 3px;
  border-radius: 0;
  color: inherit;
  background: transparent;
}

.personality-card b {
  overflow: hidden;
  font-size: 10px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.personality-card small {
  display: -webkit-box;
  overflow: hidden;
  color: var(--muted);
  font-size: 8px;
  line-height: 1.4;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.personality-selected {
  display: grid;
  width: 17px;
  height: 17px;
  place-items: center;
  margin-top: 5px;
  border-radius: 50%;
  color: white;
  background: var(--orange);
}

.personality-details {
  align-items: start;
  margin-top: 1px;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 16px;
  background: #f4f7f2;
}

.personality-preview {
  margin-top: 14px;
  border-color: #d7e5d8;
  background: #f0f8f3;
}

.prompt-section-header {
  align-items: center;
}

.prompt-section-header > :deep(.vt-button) {
  white-space: nowrap;
}

.prompt-token-bar {
  margin-bottom: 13px;
  border-color: var(--line);
  background: #f7f9f5;
}

.prompt-editor-grid {
  grid-template-columns: minmax(0, 1.08fr) minmax(300px, .92fr);
  gap: 12px;
  border: 0;
  padding: 0;
  background: transparent;
}

.prompt-editor-grid > :deep(.vt-field) {
  gap: 8px;
  border: 1px solid var(--line);
  border-radius: 15px;
  padding: 13px;
  background: #f7f9f5;
  box-shadow: none;
}

.prompt-editor-grid > :deep(.vt-field) .prompt-template-input {
  min-height: 465px;
  border-color: var(--line);
  border-radius: 11px;
  background: var(--paper-strong);
}

.prompt-render-preview {
  min-height: 512px;
  border-color: var(--line);
  border-radius: 15px;
  color: var(--ink);
  background: #f7f9f5;
  box-shadow: none;
}

.prompt-render-preview header {
  padding: 13px 14px 11px;
  background: linear-gradient(180deg, #fffdfa, #f3f7f2);
}

.prompt-render-preview pre {
  max-height: 465px;
  margin: 0 12px 12px;
  border: 1px solid var(--line);
  border-radius: 11px;
  padding: 13px;
  color: var(--ink-2);
  background: #eef2ec;
}

.agent-runtime-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.agent-runtime-card {
  display: grid;
  align-content: start;
  gap: 16px;
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 16px;
  background: #f7f9f5;
}

.agent-runtime-card > header {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.agent-runtime-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: 10px;
  color: var(--navy-2);
  background: var(--blue);
}

.agent-runtime-card > header > div {
  display: grid;
  gap: 3px;
}

.agent-runtime-card > header b {
  font-size: 11px;
}

.agent-runtime-card > header small {
  color: var(--muted);
  font-size: 8px;
}

.agent-runtime-card .form-grid {
  gap: 12px;
}

.sticky-publish {
  position: sticky;
  bottom: 16px;
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  border: 0;
  border-radius: 18px;
  padding: 14px 16px;
  color: white;
  background: var(--navy);
  box-shadow: 0 16px 42px rgba(16, 44, 51, .2);
}

.publish-mark {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 11px;
  color: var(--navy);
  background: var(--lime);
}

.sticky-publish > div {
  display: grid;
  gap: 3px;
}

.sticky-publish b {
  color: white;
  font-size: 11px;
}

.sticky-publish small {
  color: #9fb5b7;
  font-size: 8px;
}

.sticky-publish .inline-error {
  max-width: 280px;
  margin: 0;
  color: #ffc6b7;
}

.publish-target {
  display: grid;
  justify-items: end;
  gap: 2px;
  min-width: 44px;
}

.publish-target small {
  color: #8ca6a9;
  font-size: 7px;
  letter-spacing: .12em;
}

.publish-target b {
  color: var(--lime);
  font-size: 18px;
}

/* The personality library and prompt workbench share the same two-level surface. */
.personality-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.personality-card {
  position: relative;
  display: block;
  min-width: 0;
  min-height: 0;
  gap: 0;
  align-items: normal;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 15px;
  padding: 0;
  background: #fbfcf9;
  box-shadow: none;
}

.personality-card::after {
  display: none;
}

.personality-card.active {
  border-color: var(--orange);
  background: #fff8f5;
  box-shadow: 0 0 0 3px rgba(242, 100, 60, .1);
}

.personality-choice {
  display: grid;
  width: 100%;
  height: 100%;
  grid-template-columns: 32px minmax(0, 1fr) 18px;
  align-items: start;
  gap: 10px;
  border: 0;
  padding: 13px 14px;
  color: var(--ink);
  background: transparent;
  text-align: left;
}

.personality-card.deletable .personality-choice {
  padding-bottom: 30px;
}

.personality-choice:hover,
.personality-choice:focus-visible {
  background: rgba(255, 255, 255, .62);
}

.personality-choice:focus-visible {
  outline: 2px solid var(--orange);
  outline-offset: -3px;
}

.personality-card > .personality-choice .personality-mark {
  position: static;
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 10px;
  color: color-mix(in srgb, var(--personality-accent) 78%, var(--navy));
  background: color-mix(in srgb, var(--personality-accent) 14%, white);
  font-size: 11px;
  font-weight: 800;
}

.personality-card > .personality-choice .personality-copy {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.personality-card-meta {
  color: var(--personality-accent);
  font-size: 7px;
  font-weight: 800;
  letter-spacing: .12em;
}

.personality-card b {
  display: block;
  overflow: visible;
  font-size: 11px;
  line-height: 1.35;
  text-overflow: clip;
  white-space: normal;
}

.personality-card small {
  display: block;
  overflow: visible;
  color: var(--muted);
  font-size: 9px;
  line-height: 1.45;
  text-overflow: clip;
  white-space: normal;
}

.personality-selected {
  display: grid;
  width: 18px;
  height: 18px;
  place-items: center;
  align-self: center;
  margin: 0;
  border-radius: 50%;
  color: white;
  background: var(--orange);
}

.personality-delete {
  position: absolute;
  right: 10px;
  bottom: 9px;
  display: grid;
  width: 23px;
  height: 23px;
  place-items: center;
  border: 1px solid #efc9c0;
  border-radius: 7px;
  color: #b65342;
  background: #fff7f4;
}

.personality-delete:hover,
.personality-delete:focus-visible {
  border-color: #c45e4b;
  color: white;
  background: #c45e4b;
}

.personality-feature-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid #cfe2d5;
  border-radius: 999px;
  padding: 6px 9px;
  color: var(--success);
  background: rgba(255, 255, 255, .62);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: .05em;
  text-transform: uppercase;
}

.prompt-editor-grid {
  --prompt-pane-height: 430px;
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(300px, .92fr);
  align-items: stretch;
  gap: 14px;
  border: 0;
  padding: 0;
  background: transparent;
}

.prompt-workbench-pane,
.prompt-render-preview {
  display: grid;
  min-width: 0;
  min-height: 0;
  height: var(--prompt-pane-height);
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 15px;
  background: #f7f9f5;
  box-shadow: none;
}

.prompt-workbench-pane {
  padding: 0 12px 12px;
}

.prompt-pane-header,
.prompt-render-preview > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 13px 2px 11px;
}

.prompt-pane-header > div,
.prompt-render-preview > header > div {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.prompt-pane-kicker,
.prompt-render-preview > header span {
  color: var(--orange-dark);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: .12em;
}

.prompt-pane-header b {
  font-size: 11px;
}

.prompt-pane-header small,
.prompt-render-preview > header small {
  color: var(--muted);
  font-size: 8px;
  line-height: 1.45;
}

.prompt-workbench-pane .prompt-template-input {
  width: 100%;
  min-height: 0;
  height: 100%;
  resize: none;
  border-color: var(--line);
  border-radius: 11px;
  background: var(--paper-strong);
}

.prompt-render-preview > header {
  border-bottom: 1px solid var(--line);
  padding-inline: 14px;
  background: linear-gradient(180deg, #fffdfa, #f3f7f2);
}

.prompt-render-preview > header span {
  color: var(--orange);
}

.prompt-render-preview pre {
  min-height: 0;
  max-height: none;
  margin: 12px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 11px;
  padding: 13px;
  color: var(--ink-2);
  background: #eef2ec;
}

.delete-personality-confirm {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  border: 1px solid #f0d0c8;
  border-radius: 14px;
  padding: 14px;
  background: #fff7f4;
}

.delete-personality-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #b65342;
  background: #fbe2db;
}

.delete-personality-confirm b {
  font-size: 13px;
}

.delete-personality-confirm p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.55;
}

.personality-accent-field {
  display: grid;
  min-width: 0;
  gap: 9px;
  border: 0;
  margin: 0;
  padding: 0;
}

.personality-accent-field legend {
  margin-bottom: 9px;
  color: var(--ink-2);
  font-size: 12px;
  font-weight: 700;
}

.personality-accent-picker {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.personality-accent-picker button {
  display: grid;
  min-width: 0;
  min-height: 52px;
  grid-template-columns: 18px minmax(0, 1fr) 14px;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 8px 9px;
  color: var(--muted);
  background: var(--paper-strong);
  text-align: left;
}

.personality-accent-picker button:hover,
.personality-accent-picker button:focus-visible {
  border-color: var(--accent-swatch);
  color: var(--ink);
}

.personality-accent-picker button.active {
  border-color: var(--accent-swatch);
  color: var(--ink);
  background: color-mix(in srgb, var(--accent-swatch) 7%, white);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-swatch) 13%, transparent);
}

.personality-accent-picker i {
  width: 16px;
  height: 16px;
  border: 3px solid white;
  border-radius: 50%;
  background: var(--accent-swatch);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent-swatch) 45%, var(--line));
}

.personality-accent-picker span {
  overflow: hidden;
  font-size: 9px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.personality-accent-picker .vt-icon {
  color: var(--accent-swatch);
}

.personality-create-preview {
  --preview-accent: #e06b51;
  position: relative;
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  align-items: center;
  gap: 11px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--preview-accent) 45%, var(--line));
  border-radius: 14px;
  padding: 12px 14px 12px 17px;
  background: color-mix(in srgb, var(--preview-accent) 6%, white);
}

.personality-create-preview::before {
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--preview-accent);
  content: "";
}

.personality-create-preview-mark {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 12px;
  color: color-mix(in srgb, var(--preview-accent) 76%, var(--navy));
  background: color-mix(in srgb, var(--preview-accent) 15%, white);
  font-size: 13px;
  font-weight: 800;
}

.personality-create-preview > div {
  display: grid;
  min-width: 0;
  gap: 3px;
}

.personality-create-preview small {
  color: var(--preview-accent);
  font-size: 7px;
  font-weight: 800;
  letter-spacing: .11em;
}

.personality-create-preview b {
  font-size: 12px;
}

.personality-create-preview p {
  overflow: hidden;
  margin: 0;
  color: var(--muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 960px) {
  .agent-runtime-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .agent-config-nav {
    display: flex;
    overflow-x: auto;
  }

  .agent-config-nav a {
    flex: 0 0 154px;
  }

  .agent-section-header {
    grid-template-columns: 36px minmax(0, 1fr);
    align-items: start;
    padding: 18px;
  }

  .agent-section-index {
    width: 34px;
    height: 34px;
    border-radius: 10px;
  }

  .agent-section-header > :deep(.vt-button) {
    grid-column: 2;
    justify-self: start;
    margin-top: 5px;
  }

  .agent-section-content {
    padding: 18px;
  }

  .personality-feature {
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .personality-feature-mark {
    width: 40px;
    height: 40px;
    border-radius: 12px;
  }

  .personality-feature > :deep(.vt-badge) {
    grid-column: 2;
    justify-self: start;
  }

  .prompt-editor-grid {
    --prompt-pane-height: 390px;
    grid-template-columns: 1fr;
  }

  .prompt-editor-grid > :deep(.vt-field) .prompt-template-input,
  .prompt-render-preview {
    min-height: 0;
  }

  .prompt-render-preview pre {
    max-height: 330px;
  }

  .prompt-workbench-pane,
  .prompt-render-preview {
    height: var(--prompt-pane-height);
  }

  .sticky-publish {
    position: static;
    grid-template-columns: 36px minmax(0, 1fr);
    align-items: start;
  }

  .sticky-publish .publish-target {
    grid-column: 2;
    justify-items: start;
  }

  .sticky-publish .inline-error {
    grid-column: 1 / -1;
    max-width: none;
  }

  .sticky-publish > :deep(.vt-button) {
    grid-column: 1 / -1;
    width: 100%;
  }
}

@media (max-width: 520px) {
  .personality-feature {
    grid-template-columns: 42px minmax(0, 1fr);
  }

  .personality-feature-state {
    grid-column: 2;
    justify-self: start;
  }

  .prompt-editor-grid {
    --prompt-pane-height: 360px;
  }

  .prompt-workbench-pane,
  .prompt-render-preview {
    height: var(--prompt-pane-height);
  }

  .personality-accent-picker {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
