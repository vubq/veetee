<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import type { Agent, AgentPromptCatalog, PersonalityPreset, Provider } from "../../api/schemas";
import type { AgentDraftInput } from "../../types/manager";
import { voiceQualityWarnings as collectVoiceQualityWarnings } from "../../utils/voice-quality";
import { VtBadge, VtButton, VtDialog, VtEmptyState, VtField, VtIcon, VtInput, VtMetricStrip, VtOperationsHero, VtPageHeader, VtSelect, VtTextarea } from "../ui";

const props = defineProps<{
  agents: Agent[];
  providers: Provider[];
  promptCatalog: AgentPromptCatalog | undefined;
  publishAgent: (input: AgentDraftInput) => Promise<void>;
  createAgent: (input: { name: string; defaultLocale: string; interactionMode: Agent["interactionMode"]; persona: string; draftConfig?: Record<string, unknown> }) => Promise<Agent>;
  createPersonalityPreset: (input: { label: string; summary: string; accent: string; instructions: string }) => Promise<PersonalityPreset>;
  deletePersonalityPreset: (id: string) => Promise<void>;
}>();
const { t } = useI18n();
const defaultLanguage = () => t("agents.defaults.language");
const defaultResponseStyle = () => t("agents.defaults.responseStyle");

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
  language: defaultLanguage(),
  mode: "auto" as Agent["interactionMode"],
  persona: "",
  personalityPresetId: "",
});
const personalityForm = reactive({
  label: "",
  summary: "",
  accent: "coral" as "coral" | "sun" | "cyan" | "lime" | "violet" | "navy" | "pink",
  instructions: "",
});
const personalityAccents = [
  { id: "coral", labelKey: "agents.accents.coral", color: "#e06b51" },
  { id: "sun", labelKey: "agents.accents.sun", color: "#c99324" },
  { id: "cyan", labelKey: "agents.accents.cyan", color: "#287f8e" },
  { id: "lime", labelKey: "agents.accents.lime", color: "#7e9b2f" },
  { id: "violet", labelKey: "agents.accents.violet", color: "#735b91" },
  { id: "navy", labelKey: "agents.accents.navy", color: "#173e49" },
  { id: "pink", labelKey: "agents.accents.pink", color: "#c25973" },
] as const;
const form = reactive({
  name: "", locale: "vi-VN", mode: "auto" as Agent["interactionMode"], persona: "",
  language: defaultLanguage(), timeZone: browserTimeZone(), timeZoneSource: "device" as "device" | "fixed",
  personalityPresetId: "", customPersonality: "",
  responseStyle: defaultResponseStyle(),
  userAddress: "", promptTemplate: "",
  firstInput: 180, betweenTurns: 180, closingGrace: 5, maxSession: 0,
  vad: "", asr: "", llm: "", tts: "",
  voiceId: "", voiceGender: "female", voiceStyle: "tu_nhien",
  voiceRate: 1, voicePitch: 0, voiceVolume: 1,
});

const selected = computed(() => props.agents.find((agent) => agent.id === selectedId.value) ?? props.agents[0]);
const agentMetrics = computed(() => {
  const agent = selected.value;
  if (!agent) return [];
  return [
    {
      label: t("agents.metrics.draft"),
      value: `v${agent.version}`,
      detail: t(agent.version === agent.publishedVersion ? "agents.metrics.matchesPublished" : "agents.metrics.unpublishedChanges"),
      tone: agent.version === agent.publishedVersion ? "success" as const : "warning" as const,
    },
    {
      label: t("agents.metrics.published"),
      value: `v${agent.publishedVersion}`,
      detail: t("agents.metrics.immutableSnapshot"),
      tone: agent.publishedVersion ? "info" as const : "warning" as const,
    },
    {
      label: t("agents.metrics.mode"),
      value: agent.interactionMode,
      detail: t("agents.metrics.currentLocale", { locale: agent.defaultLocale }),
      tone: "neutral" as const,
    },
  ];
});
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
const ttsProvider = computed(() => props.providers.find((provider) => provider.id === form.tts));
const ttsSupportsPitch = computed(() => ttsProvider.value?.config?.supportsPitch !== false);
const voiceOptions = computed(() => {
  const voices = ttsProvider.value?.config?.voices;
  if (!Array.isArray(voices)) return [];
  return voices.filter((voice): voice is Record<string, unknown> => Boolean(voice) && typeof voice === "object" && !Array.isArray(voice));
});
const voiceStyleOptions = computed(() => {
  const styles = ttsProvider.value?.config?.styles;
  if (!Array.isArray(styles)) return [];
  return styles.filter(
    (style): style is Record<string, unknown> =>
      Boolean(style) && typeof style === "object" && !Array.isArray(style),
  );
});
const selectedVoiceSourceStyle = computed(() => {
  const voice = voiceOptions.value.find((candidate) => String(candidate.id) === form.voiceId);
  return stringValue(voice?.style);
});
const voiceQualityWarnings = computed(() => collectVoiceQualityWarnings({
  adapter: ttsProvider.value?.adapter ?? "",
  rate: Number(form.voiceRate),
  volume: Number(form.voiceVolume),
  sourceStyle: selectedVoiceSourceStyle.value,
  selectedStyle: form.voiceStyle,
}));

watch(
  () => form.tts,
  (providerId) => {
    const provider = props.providers.find((candidate) => candidate.id === providerId);
    const voices = Array.isArray(provider?.config?.voices)
      ? provider.config.voices.filter(
          (voice): voice is Record<string, unknown> =>
            Boolean(voice) && typeof voice === "object" && !Array.isArray(voice),
        )
      : [];
    form.voiceId = stringValue(provider?.config?.voice) || stringValue(voices[0]?.id);
    form.voiceGender = stringValue(provider?.config?.gender, stringValue(voices[0]?.gender, "female"));
    form.voiceStyle = stringValue(provider?.config?.style, "tu_nhien");
    form.voiceRate = Number(provider?.config?.rate ?? 1);
    form.voicePitch = provider?.config?.supportsPitch === false
      ? 0
      : Number(provider?.config?.pitchHz ?? 0);
    form.voiceVolume = Number(provider?.config?.volume ?? 1);
  },
  { flush: "sync" },
);

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
  return personalityPresets.value[0]?.id ?? "";
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
  const presetId = value === undefined
    ? defaultPersonalityId()
    : stringValue(prompt.personalityPresetId);
  return {
    schemaVersion: 1,
    template: stringValue(prompt.template, props.promptCatalog?.defaultTemplate ?? ""),
    language: stringValue(prompt.language, locale),
    timeZone: stringValue(prompt.timeZone, browserTimeZone()),
    timeZoneSource: prompt.timeZoneSource === "fixed" ? "fixed" : "device",
    personalityPresetId: personalityPresets.value.some((preset) => preset.id === presetId)
      ? presetId
      : "",
    customPersonality: stringValue(prompt.customPersonality),
    responseStyle: stringValue(
      prompt.responseStyle,
      defaultResponseStyle(),
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
    form.firstInput = Number(conversation.firstInputSeconds ?? 180);
    form.betweenTurns = Number(conversation.betweenTurnsSeconds ?? 180);
    form.closingGrace = Number(conversation.closingGraceSeconds ?? 5);
    form.maxSession = Number(conversation.maxSessionSeconds ?? 0);
    form.vad = chainProvider(agent, "vad");
    form.asr = chainProvider(agent, "asr");
    form.llm = chainProvider(agent, "llm");
    form.tts = chainProvider(agent, "tts");
    const voice = objectValue(agent.draftConfig.voice);
    const selectedTts = props.providers.find((provider) => provider.id === form.tts);
    const configuredVoice = stringValue(voice.voiceId);
    const fallbackVoice = stringValue(selectedTts?.config?.voice) || stringValue(voiceOptions.value[0]?.id);
    form.voiceId = configuredVoice || fallbackVoice;
    form.voiceGender = stringValue(voice.gender, stringValue(voiceOptions.value[0]?.gender, "female"));
    form.voiceStyle = stringValue(voice.style, stringValue(selectedTts?.config?.style, "tu_nhien"));
    form.voiceRate = Number(voice.rate ?? selectedTts?.config?.rate ?? 1);
    form.voicePitch = Number(voice.pitchHz ?? selectedTts?.config?.pitchHz ?? 0);
    form.voiceVolume = Number(voice.volume ?? selectedTts?.config?.volume ?? 1);
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
          // Product conversation has no parent turn ceiling; provider deadlines stay internal.
          totalTurnSeconds: 0,
        },
        ...(form.tts && form.voiceId
          ? {
              voice: {
                providerId: form.tts,
                voiceId: form.voiceId,
                gender: form.voiceGender || undefined,
                style: form.voiceStyle,
                rate: Number(form.voiceRate),
                pitchHz: Number(form.voicePitch),
                volume: Number(form.voiceVolume),
              },
            }
          : {}),
      },
    });
  } catch (exception) {
    error.value = exception instanceof Error ? exception.message : t("agents.errors.publishFailed");
  } finally {
    busy.value = false;
  }
}

async function create(): Promise<void> {
  if (!createForm.name.trim() || !createForm.language.trim()) {
    createError.value = t("agents.errors.nameLanguageRequired");
    return;
  }
  if (!props.promptCatalog) {
    createError.value = t("agents.errors.catalogLoading");
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
          responseStyle: defaultResponseStyle(),
          userAddress: "",
        },
      },
    });
    selectedId.value = agent.id;
    createOpen.value = false;
    createForm.name = "";
    createForm.persona = "";
    createForm.personalityPresetId = "";
  } catch (exception) {
    createError.value = exception instanceof Error ? exception.message : t("agents.errors.createFailed");
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
    personalityError.value = t("agents.errors.personalityRequired");
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
      exception instanceof Error ? exception.message : t("agents.errors.personalityCreateFailed");
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
      form.personalityPresetId = "";
    }
    if (createForm.personalityPresetId === preset.id) {
      createForm.personalityPresetId = "";
    }
    deletePersonalityOpen.value = false;
    personalityToDelete.value = null;
  } catch (exception) {
    deletePersonalityError.value =
      exception instanceof Error
        ? exception.message
        : t("agents.errors.personalityDeleteFailed");
  } finally {
    deletePersonalityBusy.value = false;
  }
}

function addVariable(name: string): void {
  const token = `{{${name}}}`;
  const separator = form.promptTemplate && !form.promptTemplate.endsWith("\n") ? "\n" : "";
  form.promptTemplate += `${separator}${token}`;
}

function moveRadioSelection<T extends string>(
  items: readonly T[],
  currentIndex: number,
  direction: -1 | 1,
  select: (item: T) => void,
  groupSelector: string,
): void {
  if (!items.length) return;
  const index = (currentIndex + direction + items.length) % items.length;
  select(items[index]!);
  void nextTick(() => {
    document.querySelector<HTMLElement>(`${groupSelector} [role="radio"][tabindex="0"]`)?.focus();
  });
}

function movePersonality(direction: -1 | 1): void {
  const ids = ["", ...personalityPresets.value.map((preset) => preset.id)];
  moveRadioSelection(ids, Math.max(0, ids.indexOf(form.personalityPresetId)), direction,
    (id) => { form.personalityPresetId = id; }, ".personality-grid");
}

function movePersonalityAccent(direction: -1 | 1): void {
  const accents = personalityAccents.map((accent) => accent.id);
  moveRadioSelection(accents, Math.max(0, accents.indexOf(personalityForm.accent)), direction,
    (accent) => { personalityForm.accent = accent; }, ".personality-accent-picker");
}

function scrollToSection(id: string): void {
  document.getElementById(id)?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block: "start",
  });
}

function resetPromptTemplate(): void {
  if (props.promptCatalog) form.promptTemplate = props.promptCatalog.defaultTemplate;
}

const promptPreview = computed(() => {
  const now = new Date();
  const previewTimeZone = form.timeZoneSource === "device" ? browserTimeZone() : form.timeZone;
  let currentDate = t("agents.preview.invalidTimeZone");
  let currentTime = t("agents.preview.invalidTimeZone");
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
    device_timezone_offset: t("agents.preview.deviceUtcOffset"),
    available_tools: t("agents.preview.toolCatalog"),
  };
  return form.promptTemplate.replace(
    /{{\s*([a-z_][a-z0-9_]*)\s*}}/g,
    (token, name: string) => values[name] ?? token,
  );
});
</script>

<template>
  <section class="vt-page" data-page="agents">
    <VtPageHeader :eyebrow="t('pages.agents.eyebrow')" :title="t('pages.agents.title')" :description="t('pages.agents.description')">
      <template #actions><VtButton @click="createOpen = true"><VtIcon name="plus" :size="16" /> {{ t("agents.actions.create") }}</VtButton></template>
    </VtPageHeader>

    <div v-if="agents.length" class="agent-layout">
      <aside class="agent-list">
        <button v-for="agent in agents" :key="agent.id" type="button" :class="{ active: selected?.id === agent.id }" @click="selectedId = agent.id">
          <span class="agent-list-avatar">{{ agent.name.slice(0, 1).toUpperCase() }}</span><span><b>{{ agent.name }}</b><small>{{ agent.defaultLocale }} · {{ agent.interactionMode }}</small></span><VtBadge :tone="agent.version === agent.publishedVersion ? 'success' : 'warning'">v{{ agent.publishedVersion }}</VtBadge>
        </button>
      </aside>

      <form v-if="selected" class="agent-editor" @submit.prevent="publish">
        <div class="agent-editor-dashboard" data-page-section="assistant-summary">
          <VtOperationsHero
            :eyebrow="t('agents.hero.eyebrow', { id: selected.id })"
            :title="form.name"
            :description="t('agents.hero.description')"
            :value="`v${selected.publishedVersion}`"
            :value-label="t('agents.hero.published')"
            :value-hint="t(selected.version === selected.publishedVersion ? 'agents.hero.synced' : 'agents.hero.draftChanged')"
            icon="agent"
          />
          <VtMetricStrip :items="agentMetrics" />
        </div>

        <nav class="agent-config-nav" :aria-label="t('agents.nav.label')">
          <a href="#agent-identity" @click.prevent="scrollToSection('agent-identity')"><span>01</span><div><b>{{ t("agents.nav.identity") }}</b><small>{{ t("agents.nav.identityShort") }}</small></div></a>
          <a href="#agent-personality" @click.prevent="scrollToSection('agent-personality')"><span>02</span><div><b>{{ t("agents.nav.personality") }}</b><small>{{ t("agents.nav.personalityShort") }}</small></div></a>
          <a href="#agent-prompt" @click.prevent="scrollToSection('agent-prompt')"><span>03</span><div><b>{{ t("agents.nav.prompt") }}</b><small>{{ t("agents.nav.promptShort") }}</small></div></a>
          <a href="#agent-runtime" @click.prevent="scrollToSection('agent-runtime')"><span>04</span><div><b>{{ t("agents.nav.runtime") }}</b><small>{{ t("agents.nav.runtimeShort") }}</small></div></a>
        </nav>

        <article id="agent-identity" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">01</span>
            <div><span class="vt-kicker">{{ t("agents.identity.kicker") }}</span><h2>{{ t("agents.identity.title") }}</h2><p>{{ t("agents.identity.description") }}</p></div>
          </header>
          <div class="agent-section-content">
          <div class="form-grid two">
            <VtField  :label="t('agents.identity.name')"  :hint="t('agents.identity.nameHint')" required><VtInput v-model="form.name" maxlength="80" required /></VtField>
            <VtField  :label="t('agents.identity.defaultLocale')"  :hint="t('agents.identity.defaultLocaleHint')" required><VtInput v-model="form.locale" maxlength="35" placeholder="vi-VN" required /></VtField>
            <VtField  :label="t('agents.identity.aiLanguage')"  :hint="t('agents.identity.aiLanguageHint')" required><VtInput v-model="form.language" maxlength="120"  :placeholder="t('agents.identity.aiLanguagePlaceholder')" required /></VtField>
            <VtField  :label="t('agents.identity.timeZoneSource')"  :hint="t('agents.identity.timeZoneSourceHint')" required><VtSelect v-model="form.timeZoneSource"><option value="device">{{ t("agents.identity.deviceTimeZone") }}</option><option value="fixed">{{ t("agents.identity.fixedTimeZone") }}</option></VtSelect></VtField>
            <VtField v-if="form.timeZoneSource === 'fixed'"  :label="t('agents.identity.timeZoneFallback')"  :hint="t('agents.identity.timeZoneFallbackHint')" required><VtInput v-model="form.timeZone" maxlength="80" placeholder="Asia/Bangkok" required /></VtField>
            <div v-else class="agent-mode-note span-two"><VtIcon name="check" :size="18" /><p><b>{{ t("agents.identity.timeZoneNoteTitle") }}</b><span>{{ t("agents.identity.timeZoneNoteBody") }}</span></p></div>
            <VtField  :label="t('agents.identity.mode')" class="span-two"  :hint="t('agents.identity.modeHint')"><VtSelect v-model="form.mode"><option value="auto">{{ t("agents.identity.autoMode") }}</option><option value="realtime">{{ t("agents.identity.realtimeMode") }}</option><option value="manual">{{ t("agents.identity.manualMode") }}</option></VtSelect></VtField>
            <div v-if="form.mode === 'realtime'" class="agent-mode-note span-two"><VtIcon name="warning" :size="18" /><p><b>{{ t("agents.identity.realtimeTitle") }}</b><span>{{ t("agents.identity.realtimeBody") }}</span></p></div>
          </div>
          </div>
        </article>

        <article id="agent-personality" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">02</span>
            <div><span class="vt-kicker">{{ t("agents.personality.kicker") }}</span><h2>{{ t("agents.personality.title") }}</h2><p>{{ t("agents.personality.description") }}</p></div>
            <VtButton type="button" variant="quiet" size="sm" data-testid="create-personality" @click="openPersonalityCreate"><VtIcon name="plus" :size="15" /> {{ t("agents.personality.add") }}</VtButton>
          </header>
          <div class="agent-section-content">
          <div v-if="selectedPersonality" class="personality-feature">
            <span class="personality-feature-mark">{{ selectedPersonality.label.slice(0, 1) }}</span>
            <div><span class="personality-feature-kicker">{{ t(selectedPersonality.builtIn ? "agents.personality.libraryPreset" : "agents.personality.customPreset") }}</span><h3>{{ selectedPersonality.label }}</h3><p>{{ selectedPersonality.summary }}</p></div>
            <span class="personality-feature-state"><VtIcon name="check" :size="14" /> {{ t("agents.personality.selected") }}</span>
          </div>
          <div class="personality-grid" role="radiogroup" :aria-label="t('agents.personality.groupLabel')">
            <div :class="['personality-card', 'personality-none', { active: !form.personalityPresetId }]">
              <button
                type="button"
                class="personality-choice"
                role="radio"
                :aria-checked="!form.personalityPresetId"
                :tabindex="!form.personalityPresetId ? 0 : -1"
                @click="form.personalityPresetId = ''"
                @keydown.left.prevent="movePersonality(-1)"
                @keydown.right.prevent="movePersonality(1)"
                @keydown.up.prevent="movePersonality(-1)"
                @keydown.down.prevent="movePersonality(1)"
              >
                <span class="personality-mark" aria-hidden="true">—</span>
                <span class="personality-copy">
                  <span class="personality-card-meta">{{ t("agents.personality.optional") }}</span>
                  <b>{{ t("agents.personality.none") }}</b>
                  <small>{{ t("agents.personality.noneDescription") }}</small>
                </span>
                <i v-if="!form.personalityPresetId" class="personality-selected" aria-hidden="true"><VtIcon name="check" :size="13" /></i>
              </button>
            </div>
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
                :tabindex="form.personalityPresetId === preset.id ? 0 : -1"
                @click="form.personalityPresetId = preset.id"
                @keydown.left.prevent="movePersonality(-1)"
                @keydown.right.prevent="movePersonality(1)"
                @keydown.up.prevent="movePersonality(-1)"
                @keydown.down.prevent="movePersonality(1)"
              >
                <span class="personality-mark" aria-hidden="true">{{ preset.label.slice(0, 1) }}</span>
                <span class="personality-copy">
                  <span class="personality-card-meta">{{ t(preset.builtIn ? "agents.personality.library" : "agents.personality.custom") }}</span>
                  <b>{{ preset.label }}</b>
                  <small>{{ preset.summary }}</small>
                </span>
                <i v-if="form.personalityPresetId === preset.id" class="personality-selected" aria-hidden="true"><VtIcon name="check" :size="13" /></i>
              </button>
              <button
                v-if="preset.deletable && !preset.builtIn"
                type="button"
                class="personality-delete"
                :aria-label="t('agents.personality.deleteLabel', { label: preset.label })"
                @click.stop="askDeletePersonality(preset)"
              >
                <VtIcon name="trash" :size="14" />
              </button>
            </div>
          </div>
          <div class="form-grid two personality-details">
            <VtField :label="t('agents.personality.introduction')" :hint="t('agents.personality.introductionHint')"><VtTextarea v-model="form.persona" rows="5" :placeholder="t('agents.personality.introductionPlaceholder')" /></VtField>
            <VtField :label="t('agents.personality.refinement')" :hint="t('agents.personality.refinementHint')"><VtTextarea v-model="form.customPersonality" rows="5" maxlength="4000" :placeholder="t('agents.personality.refinementPlaceholder')" /></VtField>
            <VtField :label="t('agents.personality.responseStyle')" :hint="t('agents.personality.responseStyleHint')"><VtTextarea v-model="form.responseStyle" rows="3" maxlength="2000" /></VtField>
            <VtField :label="t('agents.personality.userAddress')" :hint="t('agents.personality.userAddressHint')"><VtInput v-model="form.userAddress" maxlength="120" :placeholder="t('agents.personality.userAddressPlaceholder')" /></VtField>
          </div>
          <div v-if="selectedPersonality" class="personality-preview">
            <span>{{ t("agents.personality.frozen") }}</span>
            <p>{{ selectedPersonality.instructions }}</p>
          </div>
          </div>
        </article>

        <article id="agent-prompt" class="vt-panel form-section agent-config-section prompt-section">
          <header class="agent-section-header prompt-section-header">
            <span class="agent-section-index">03</span>
            <div><span class="vt-kicker">{{ t("agents.prompt.kicker") }}</span><h2>{{ t("agents.prompt.title") }}</h2><p>{{ t("agents.prompt.description") }}</p></div>
            <VtButton type="button" variant="quiet" size="sm" @click="resetPromptTemplate"><VtIcon name="refresh" :size="15" /> {{ t("agents.prompt.reset") }}</VtButton>
          </header>
          <div class="agent-section-content">
          <div class="prompt-token-bar">
            <div class="prompt-token-heading">
              <b>{{ t("agents.prompt.insertTitle") }}</b>
              <small>{{ t("agents.prompt.insertDescription") }}</small>
            </div>
            <div class="prompt-variables" :aria-label="t('agents.prompt.variablesLabel')">
              <button v-for="variable in promptCatalog?.variables ?? []" :key="variable.name" type="button" :title="variable.description" @click="addVariable(variable.name)">
                <code v-text="`{{${variable.name}}}`"></code>
                <span>{{ t(variable.required ? "agents.prompt.required" : variable.dynamic ? "agents.prompt.runtime" : "agents.prompt.optional") }}</span>
              </button>
            </div>
          </div>
          <div class="prompt-editor-grid">
            <section class="prompt-workbench-pane">
              <header class="prompt-pane-header">
                <div><span class="prompt-pane-kicker">{{ t("agents.prompt.sourceKicker") }}</span><b>{{ t("agents.prompt.draftTitle") }}</b><small>{{ t("agents.prompt.draftDescription") }}</small></div>
                <VtBadge tone="warning">{{ t("agents.prompt.unpublished") }}</VtBadge>
              </header>
              <VtTextarea v-model="form.promptTemplate" class="prompt-template-input" :aria-label="t('agents.prompt.draftTitle')" rows="18" maxlength="20000" spellcheck="false" required />
            </section>
            <section class="prompt-render-preview">
              <header><div><span>{{ t("agents.prompt.previewKicker") }}</span><small>{{ t("agents.prompt.previewDescription") }}</small></div><VtBadge tone="success">{{ t("agents.prompt.safe") }}</VtBadge></header>
              <pre>{{ promptPreview }}</pre>
            </section>
          </div>
          </div>
        </article>

        <article id="agent-runtime" class="vt-panel form-section agent-config-section">
          <header class="agent-section-header">
            <span class="agent-section-index">04</span>
            <div><span class="vt-kicker">{{ t("agents.runtime.kicker") }}</span><h2>{{ t("agents.runtime.title") }}</h2><p>{{ t("agents.runtime.description") }}</p></div>
          </header>
          <div class="agent-section-content agent-runtime-grid">
            <section class="agent-runtime-card">
              <header><span class="agent-runtime-icon"><VtIcon name="provider" :size="17" /></span><div><b>{{ t("agents.runtime.providerChain") }}</b><small>{{ t("agents.runtime.providerChainHint") }}</small></div></header>
              <div class="form-grid two">
                <VtField label="VAD"><VtSelect v-model="form.vad"><option value="">{{ t("agents.runtime.notSelected") }}</option><option v-for="provider in enabledProviders('vad')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="ASR"><VtSelect v-model="form.asr"><option value="">{{ t("agents.runtime.notSelected") }}</option><option v-for="provider in enabledProviders('asr')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="LLM"><VtSelect v-model="form.llm"><option value="">{{ t("agents.runtime.notSelected") }}</option><option v-for="provider in enabledProviders('llm')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
                <VtField label="TTS"><VtSelect v-model="form.tts"><option value="">{{ t("agents.runtime.notSelected") }}</option><option v-for="provider in enabledProviders('tts')" :key="provider.id" :value="provider.id">{{ provider.adapter }} · {{ provider.model }}</option></VtSelect></VtField>
              </div>
            </section>
            <section class="agent-runtime-card agent-voice-card">
              <header><span class="agent-runtime-icon"><VtIcon name="mic" :size="17" /></span><div><b>{{ t("agents.runtime.voiceTitle") }}</b><small>{{ t("agents.runtime.voiceDescription") }}</small></div></header>
              <div class="form-grid two">
                <VtField :label="t('agents.runtime.voice')"><VtSelect v-model="form.voiceId" :disabled="!form.tts"><option value="">{{ t("agents.runtime.notSelected") }}</option><option v-if="form.voiceId && !voiceOptions.some((voice) => String(voice.id) === form.voiceId)" :value="form.voiceId">{{ form.voiceId }}</option><option v-for="voice in voiceOptions" :key="String(voice.id)" :value="String(voice.id)">{{ String(voice.label ?? voice.id) }} · {{ String(voice.gender ?? "neutral") }}</option></VtSelect></VtField>
                <VtField :label="t('agents.runtime.gender')"><VtSelect v-model="form.voiceGender"><option value="female">{{ t("agents.runtime.female") }}</option><option value="male">{{ t("agents.runtime.male") }}</option><option value="neutral">{{ t("agents.runtime.neutral") }}</option></VtSelect></VtField>
                <VtField :label="t('agents.runtime.voiceStyle')" :hint="t('agents.runtime.voiceStyleHint')"><VtSelect v-model="form.voiceStyle" :disabled="!voiceStyleOptions.length"><option v-if="!voiceStyleOptions.length" value="tu_nhien">{{ t("agents.runtime.natural") }}</option><option v-for="style in voiceStyleOptions" :key="String(style.id)" :value="String(style.id)">{{ String(style.label ?? style.id) }}</option></VtSelect></VtField>
                <VtField :label="t('agents.runtime.rate')" :hint="t('agents.runtime.rateRange')"><VtInput v-model="form.voiceRate" type="number" min="0.5" max="2" step="0.05" /></VtField>
                <VtField :label="t('agents.runtime.pitch')" :hint="t(ttsSupportsPitch ? 'agents.runtime.pitchHint' : 'agents.runtime.pitchUnsupported')"><VtInput v-model="form.voicePitch" type="number" min="-100" max="100" step="1" :disabled="!ttsSupportsPitch" /></VtField>
                <VtField :label="t('agents.runtime.volume')" :hint="t('agents.runtime.volumeRange')"><VtInput v-model="form.voiceVolume" type="number" min="0" max="1.5" step="0.05" /></VtField>
              </div>
              <p v-if="form.tts && !voiceOptions.length" class="agent-runtime-hint">{{ t("agents.runtime.noVoiceCatalog") }}</p>
              <div v-if="voiceQualityWarnings.length" class="tts-quality-warnings" role="status">
                <VtIcon name="warning" :size="17" />
                <div><b>{{ t("agents.runtime.qualityRisk") }}</b><p v-for="warning in voiceQualityWarnings" :key="warning">{{ warning }}</p></div>
              </div>
            </section>
            <section class="agent-runtime-card">
              <header><span class="agent-runtime-icon"><VtIcon name="telemetry" :size="17" /></span><div><b>{{ t("agents.runtime.inactivity") }}</b><small>{{ t("agents.runtime.inactivityHint") }}</small></div></header>
              <div class="form-grid two">
                <VtField :label="t('agents.runtime.firstActivity')" :hint="t('agents.runtime.default180')"><VtInput v-model="form.firstInput" type="number" min="3" max="300" /></VtField>
                <VtField :label="t('agents.runtime.betweenTurns')" :hint="t('agents.runtime.default180')"><VtInput v-model="form.betweenTurns" type="number" min="3" max="600" /></VtField>
                <VtField :label="t('agents.runtime.closingGrace')" :hint="t('agents.runtime.closingRange')"><VtInput v-model="form.closingGrace" type="number" min="0.5" max="60" step="0.5" /></VtField>
                <VtField :label="t('agents.runtime.sessionLimit')" :hint="t('agents.runtime.unlimited')"><VtInput v-model="form.maxSession" type="number" min="0" max="3600" /></VtField>
              </div>
            </section>
          </div>
        </article>

        <div class="sticky-publish"><span class="publish-mark"><VtIcon name="upload" :size="18" /></span><div><b>{{ t("agents.publish.title") }}</b><small>{{ t("agents.publish.description") }}</small></div><p v-if="error" class="inline-error">{{ error }}</p><span class="publish-target"><small>VERSION</small><b>v{{ selected.version + 1 }}</b></span><VtButton type="submit" :busy="busy"><VtIcon name="upload" :size="17" /> {{ t("agents.publish.action", { version: selected.version + 1 }) }}</VtButton></div>
      </form>
    </div>
    <VtEmptyState v-else icon="agent" :title="t('agents.empty.title')" :text="t('agents.empty.body')" />

    <VtDialog :open="createOpen" :title="t('agents.createDialog.title')" :eyebrow="t('agents.createDialog.eyebrow')" icon="agent" :description="t('agents.createDialog.description')" width="sm" @close="createOpen = false">
      <form id="create-agent-form" class="form-stack" @submit.prevent="create">
        <VtField  :label="t('agents.identity.name')" required><VtInput v-model="createForm.name" maxlength="80" :placeholder="t('agents.createDialog.namePlaceholder')" required /></VtField>
        <div class="form-grid two">
          <VtField :label="t('agents.createDialog.locale')"><VtInput v-model="createForm.locale" maxlength="35" placeholder="vi-VN" /></VtField>
          <VtField :label="t('agents.createDialog.aiLanguage')" required><VtInput v-model="createForm.language" maxlength="120"  :placeholder="t('agents.identity.aiLanguagePlaceholder')" required /></VtField>
          <VtField :label="t('agents.createDialog.mode')"><VtSelect v-model="createForm.mode"><option value="auto">{{ t("agents.createDialog.auto") }}</option><option value="manual">{{ t("agents.createDialog.manual") }}</option><option value="realtime">{{ t("agents.createDialog.realtime") }}</option></VtSelect></VtField>
          <VtField :label="t('agents.createDialog.personality')"><VtSelect v-model="createForm.personalityPresetId"><option value="">{{ t("agents.createDialog.noPersonality") }}</option><option v-for="preset in personalityPresets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></VtSelect></VtField>
        </div>
        <VtField :label="t('agents.personality.introduction')" :hint="t('agents.createDialog.introductionHint')"><VtTextarea v-model="createForm.persona" rows="5" :placeholder="t('agents.createDialog.introductionPlaceholder')" /></VtField>
        <p v-if="createError" class="inline-error" role="alert">{{ createError }}</p>
      </form>
      <template #footer><VtButton variant="quiet" @click="createOpen = false">{{ t("common.cancel") }}</VtButton><VtButton form="create-agent-form" type="submit" :busy="createBusy"><VtIcon name="plus" :size="16" /> {{ t("agents.createDialog.submit") }}</VtButton></template>
    </VtDialog>

    <VtDialog :open="personalityOpen" :title="t('agents.personalityDialog.title')" :eyebrow="t('agents.personalityDialog.eyebrow')" icon="agent" :description="t('agents.personalityDialog.description')" width="md" @close="personalityOpen = false">
      <form id="create-personality-form" class="form-stack" @submit.prevent="createPersonality">
        <VtField :label="t('agents.personalityDialog.name')" :hint="t('agents.personalityDialog.nameHint')" required><VtInput v-model="personalityForm.label" maxlength="80" :placeholder="t('agents.personalityDialog.namePlaceholder')" required /></VtField>
        <VtField :label="t('agents.personalityDialog.summary')" :hint="t('agents.personalityDialog.summaryHint')" required><VtInput v-model="personalityForm.summary" maxlength="240" :placeholder="t('agents.personalityDialog.summaryPlaceholder')" required /></VtField>
        <fieldset class="personality-accent-field">
          <legend>{{ t("agents.personalityDialog.accent") }}</legend>
          <div class="personality-accent-picker" role="radiogroup" :aria-label="t('agents.personalityDialog.accent')">
            <button
              v-for="accent in personalityAccents"
              :key="accent.id"
              type="button"
              role="radio"
              :aria-checked="personalityForm.accent === accent.id"
              :tabindex="personalityForm.accent === accent.id ? 0 : -1"
              :class="{ active: personalityForm.accent === accent.id }"
              :style="{ '--accent-swatch': accent.color }"
              @click="personalityForm.accent = accent.id"
              @keydown.left.prevent="movePersonalityAccent(-1)"
              @keydown.right.prevent="movePersonalityAccent(1)"
              @keydown.up.prevent="movePersonalityAccent(-1)"
              @keydown.down.prevent="movePersonalityAccent(1)"
            >
              <i aria-hidden="true"></i>
              <span>{{ t(accent.labelKey) }}</span>
              <VtIcon v-if="personalityForm.accent === accent.id" name="check" :size="12" />
            </button>
          </div>
        </fieldset>
        <VtField :label="t('agents.personalityDialog.instructions')" :hint="t('agents.personalityDialog.instructionsHint')" required><VtTextarea v-model="personalityForm.instructions" rows="7" maxlength="4000" :placeholder="t('agents.personalityDialog.instructionsPlaceholder')" required /></VtField>
        <section
          class="personality-create-preview"
          :style="{ '--preview-accent': selectedPersonalityAccent.color }"
          :aria-label="t('agents.personalityDialog.previewLabel')"
        >
          <span class="personality-create-preview-mark">{{ personalityForm.label.trim().slice(0, 1) || "T" }}</span>
          <div>
            <small>{{ t("agents.personalityDialog.previewKicker") }}</small>
            <b>{{ personalityForm.label.trim() || t("agents.personalityDialog.previewName") }}</b>
            <p>{{ personalityForm.summary.trim() || t("agents.personalityDialog.previewSummary") }}</p>
          </div>
        </section>
        <p v-if="personalityError" class="inline-error" role="alert">{{ personalityError }}</p>
      </form>
      <template #footer><VtButton variant="quiet" @click="personalityOpen = false">{{ t("common.cancel") }}</VtButton><VtButton form="create-personality-form" type="submit" :busy="personalityBusy"><VtIcon name="plus" :size="16" /> {{ t("agents.personalityDialog.submit") }}</VtButton></template>
    </VtDialog>

    <VtDialog :open="deletePersonalityOpen" :title="t('agents.deleteDialog.title')" :eyebrow="t('agents.deleteDialog.eyebrow')" icon="warning" :description="t('agents.deleteDialog.description')" width="sm" @close="deletePersonalityOpen = false">
      <div class="delete-personality-confirm">
        <span class="delete-personality-mark"><VtIcon name="trash" :size="19" /></span>
        <div><b>{{ personalityToDelete?.label }}</b><p>{{ t("agents.deleteDialog.body") }}</p></div>
      </div>
      <p v-if="deletePersonalityError" class="inline-error" role="alert">{{ deletePersonalityError }}</p>
      <template #footer><VtButton variant="quiet" @click="deletePersonalityOpen = false">{{ t("common.cancel") }}</VtButton><VtButton variant="danger" :busy="deletePersonalityBusy" @click="deletePersonality"><VtIcon name="trash" :size="16" /> {{ t("agents.deleteDialog.submit") }}</VtButton></template>
    </VtDialog>
  </section>
</template>

<style scoped>
.agent-editor {
  gap: 16px;
}

.agent-editor-dashboard {
  display: grid;
  gap: 10px;
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
  background: color-mix(in srgb, var(--orange) 5%, var(--paper-strong));
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
  background: color-mix(in srgb, var(--orange) 12%, var(--paper));
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
  background: linear-gradient(135deg, var(--paper-strong), var(--paper));
}

.agent-section-index {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid #f4c4b4;
  border-radius: 13px;
  color: var(--orange-dark);
  background: color-mix(in srgb, var(--orange) 12%, var(--paper));
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
  background: color-mix(in srgb, var(--success) 8%, var(--paper));
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
  background: linear-gradient(115deg, color-mix(in srgb, var(--lime) 12%, var(--paper)), var(--paper));
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
  background: var(--paper);
  box-shadow: none;
}

.personality-card.personality-none {
  --personality-accent: var(--muted);
}

.personality-card::after {
  display: none;
}

.personality-card:hover {
  border-color: var(--line-strong);
  background: var(--paper-strong);
  transform: translateY(-1px);
}

.personality-card.active {
  border-color: var(--orange);
  background: color-mix(in srgb, var(--orange) 7%, var(--paper));
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
  background: color-mix(in srgb, var(--personality-accent) 14%, var(--paper-strong));
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
  background: var(--color-surface-inset);
}

.personality-preview {
  margin-top: 14px;
  border-color: #d7e5d8;
  background: color-mix(in srgb, var(--success) 8%, var(--paper));
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
  background: var(--color-surface-inset);
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
  background: var(--color-surface-inset);
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
  background: var(--color-surface-inset);
  box-shadow: none;
}

.prompt-render-preview header {
  padding: 13px 14px 11px;
  background: linear-gradient(180deg, var(--paper-strong), var(--paper));
}

.prompt-render-preview pre {
  max-height: 465px;
  margin: 0 12px 12px;
  border: 1px solid var(--line);
  border-radius: 11px;
  padding: 13px;
  color: var(--ink-2);
  background: var(--color-surface-inset);
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
  background: var(--color-surface-inset);
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

.agent-voice-card {
  order: 3;
  grid-column: 1 / -1;
}

.agent-runtime-card:last-child {
  order: 2;
}

.agent-runtime-hint {
  margin: 0;
  border-radius: 10px;
  padding: 9px 11px;
  color: var(--muted);
  background: var(--color-surface-inset);
  font-size: 9px;
  line-height: 1.55;
}

.tts-quality-warnings {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 9px;
  border: 1px solid #e5d3ae;
  border-radius: 12px;
  padding: 10px 11px;
  color: var(--warning);
  background: color-mix(in srgb, var(--warning) 8%, var(--paper));
}

.tts-quality-warnings > div {
  display: grid;
  gap: 4px;
}

.tts-quality-warnings b {
  font-size: 10px;
}

.tts-quality-warnings p {
  margin: 0;
  color: var(--muted);
  font-size: 8px;
  line-height: 1.45;
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
  background: var(--paper);
  box-shadow: none;
}

.personality-card::after {
  display: none;
}

.personality-card.active {
  border-color: var(--orange);
  background: color-mix(in srgb, var(--orange) 7%, var(--paper));
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
  background: color-mix(in srgb, var(--paper-strong) 70%, transparent);
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
  background: color-mix(in srgb, var(--personality-accent) 14%, var(--paper-strong));
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
  border: 1px solid color-mix(in srgb, var(--danger) 40%, var(--line));
  border-radius: 7px;
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 6%, var(--paper));
}

.personality-delete:hover,
.personality-delete:focus-visible {
  border-color: var(--danger);
  color: white;
  background: var(--danger);
}

.personality-feature-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid color-mix(in srgb, var(--success) 35%, var(--line));
  border-radius: 999px;
  padding: 6px 9px;
  color: var(--success);
  background: color-mix(in srgb, var(--paper-strong) 70%, transparent);
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
  background: var(--color-surface-inset);
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
  background: linear-gradient(180deg, var(--paper-strong), var(--paper));
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
  background: var(--color-surface-inset);
}

.delete-personality-confirm {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  border: 1px solid #f0d0c8;
  border-radius: 14px;
  padding: 14px;
  background: color-mix(in srgb, var(--danger) 6%, var(--paper));
}

.delete-personality-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border-radius: 12px;
  color: #b65342;
  background: color-mix(in srgb, var(--danger) 14%, var(--paper));
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
  background: color-mix(in srgb, var(--accent-swatch) 7%, var(--paper-strong));
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
  background: color-mix(in srgb, var(--preview-accent) 6%, var(--paper-strong));
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
  background: color-mix(in srgb, var(--preview-accent) 15%, var(--paper-strong));
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
